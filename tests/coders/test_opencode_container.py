"""Tests for the OpenCode container lifecycle: task ownership and ports."""

import io
import tarfile
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

from snodo.coders.opencode_container import (
    OpenCodeContainer,
    OpenCodeContainerError,
)


def test_concurrent_tasks_do_not_share_container(tmp_path):
    """Running containers are selected by task identity, never image alone."""
    created = []

    def run(*args, **kwargs):
        # Docker reports a published port for every container it starts;
        # a container that reports none is refused, so the fake reports one.
        container = _container(61000 + len(created))
        created.append((container, kwargs))
        return container

    client = SimpleNamespace(
        containers=SimpleNamespace(list=Mock(return_value=[]), run=run),
    )
    first = OpenCodeContainer()
    second = OpenCodeContainer()
    first._client = client
    second._client = client
    first._wait_ready = Mock()
    second._wait_ready = Mock()

    # The workspace must exist: a container is refused a path the daemon
    # could not mount.
    workspace_one = tmp_path / "one"
    workspace_two = tmp_path / "two"
    workspace_one.mkdir()
    workspace_two.mkdir()

    first.start(workspace_one, task_id="task-one")
    second.start(workspace_two, task_id="task-two")

    assert created[0][1]["labels"] == {"com.snodo.task-id": "task-one"}
    assert created[1][1]["labels"] == {"com.snodo.task-id": "task-two"}
    assert first._container is not second._container
    assert client.containers.list.call_args_list == [
        (( ), {"filters": {
            "ancestor": "snodo-opencode:latest",
            "status": "running",
            "label": "com.snodo.task-id=task-one",
        }}),
        (( ), {"filters": {
            "ancestor": "snodo-opencode:latest",
            "status": "running",
            "label": "com.snodo.task-id=task-two",
        }}),
    ]


def _container(port: int) -> MagicMock:
    result = MagicMock()
    result.attrs = {
        "NetworkSettings": {
            "Ports": {"55440/tcp": [{"HostPort": str(port)}]},
        },
    }
    result.id = f"container-{port}"
    return result


def test_containers_get_distinct_daemon_assigned_ports(tmp_path):
    client = SimpleNamespace(containers=MagicMock())
    ports = iter((61001, 61002))

    def run(*args, **kwargs):
        assert kwargs["ports"] == {"55440/tcp": 0}
        return _container(next(ports))

    client.containers.run.side_effect = run
    first = OpenCodeContainer()
    second = OpenCodeContainer()
    for container in (first, second):
        container._client = client
        container._wait_ready = MagicMock()
        container._log_readiness = MagicMock()
        container.start(tmp_path)

    assert first.base_url == f"http://{first._host}:61001"
    assert second.base_url == f"http://{second._host}:61002"
    assert first.base_url != second.base_url
    assert client.containers.list.call_count == 0


def test_unavailable_explicit_port_is_reported(tmp_path):
    client = SimpleNamespace(containers=MagicMock())
    client.containers.run.side_effect = RuntimeError(
        "port is already allocated"
    )
    container = OpenCodeContainer(port=61003)
    container._client = client

    with pytest.raises(OpenCodeContainerError, match="port is already allocated"):
        container.start(tmp_path)

    assert client.containers.run.call_args.kwargs["ports"] == {"55440/tcp": 61003}


def test_remote_daemon_receives_workspace_contents(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCKER_HOST", "tcp://daemon.example:2375")
    (tmp_path / "asked.py").write_text("print('task')")
    remote = _container(61004)
    client = SimpleNamespace(
        containers=SimpleNamespace(list=Mock(return_value=[]), run=Mock(return_value=remote)),
    )
    manager = OpenCodeContainer()
    manager._client = client
    manager._wait_ready = Mock()

    manager.start(tmp_path, task_id="remote-task")

    archive = io.BytesIO(remote.put_archive.call_args.args[1])
    with tarfile.open(fileobj=archive, mode="r:") as tar:
        assert tar.extractfile("./asked.py").read() == b"print('task')"
    assert "volumes" not in client.containers.run.call_args.kwargs


def test_remote_daemon_changes_arrive_in_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCKER_HOST", "tcp://daemon.example:2375")
    original = tmp_path / "original.py"
    original.write_text("old")
    changed = _tar_archive({"changed.py": b"new", "original.py": b"updated"})
    remote = _container(61005)
    remote.get_archive.return_value = (changed, {})
    manager = OpenCodeContainer()
    manager._container = remote

    manager.sync_workspace_from_container(tmp_path)

    assert (tmp_path / "changed.py").read_text() == "new"
    assert (tmp_path / "original.py").read_text() == "updated"


def test_remote_daemon_archive_shape_is_required_before_deletion(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCKER_HOST", "tcp://daemon.example:2375")
    original = tmp_path / "original.py"
    original.write_text("keep me")
    remote = _container(61009)
    remote.get_archive.return_value = (_tar_archive_without_workspace_root({"new.py": b"new"}), {})
    manager = OpenCodeContainer()
    manager._container = remote

    with pytest.raises(OpenCodeContainerError, match="workspace root"):
        manager.sync_workspace_from_container(tmp_path)

    assert original.read_text() == "keep me"


def test_local_daemon_keeps_bind_mount(tmp_path, monkeypatch):
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    remote = _container(61006)
    client = SimpleNamespace(
        containers=SimpleNamespace(list=Mock(return_value=[]), run=Mock(return_value=remote)),
    )
    manager = OpenCodeContainer()
    manager._client = client
    manager._wait_ready = Mock()

    manager.start(tmp_path)

    assert client.containers.run.call_args.kwargs["volumes"] == {
        str(tmp_path.resolve()): {"bind": "/workspace", "mode": "rw"},
    }
    remote.put_archive.assert_not_called()


def test_local_daemon_uses_archive_when_contained(tmp_path, monkeypatch):
    """Declared containment never substitutes the operator's bind mount."""
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    (tmp_path / "asked.py").write_text("print('task')")
    remote = _container(61010)
    client = SimpleNamespace(
        containers=SimpleNamespace(list=Mock(return_value=[]), run=Mock(return_value=remote)),
    )
    manager = OpenCodeContainer()
    manager._client = client
    manager._wait_ready = Mock()

    manager.start(tmp_path, contained=True)

    assert "volumes" not in client.containers.run.call_args.kwargs
    remote.put_archive.assert_called_once()
    assert manager._contained is True


def test_contained_workspace_push_failure_is_reported(tmp_path, monkeypatch):
    """A declared copy that cannot be transferred fails instead of mounting."""
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    remote = _container(61011)
    remote.put_archive.side_effect = OSError("copy unavailable")
    client = SimpleNamespace(
        containers=SimpleNamespace(list=Mock(return_value=[]), run=Mock(return_value=remote)),
    )
    manager = OpenCodeContainer()
    manager._client = client

    with pytest.raises(OpenCodeContainerError, match="copy workspace to remote"):
        manager.start(tmp_path, contained=True)

    assert "volumes" not in client.containers.run.call_args.kwargs


def test_remote_workspace_push_failure_is_reported(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCKER_HOST", "tcp://daemon.example:2375")
    remote = _container(61007)
    remote.put_archive.side_effect = OSError("connection lost")
    client = SimpleNamespace(
        containers=SimpleNamespace(list=Mock(return_value=[]), run=Mock(return_value=remote)),
    )
    manager = OpenCodeContainer()
    manager._client = client

    with pytest.raises(OpenCodeContainerError, match="copy workspace to remote"):
        manager.start(tmp_path)


def test_remote_workspace_pull_failure_is_reported(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCKER_HOST", "tcp://daemon.example:2375")
    remote = _container(61008)
    remote.get_archive.side_effect = OSError("connection lost")
    manager = OpenCodeContainer()
    manager._container = remote

    with pytest.raises(OpenCodeContainerError, match="copy workspace from remote"):
        manager.sync_workspace_from_container(tmp_path)


def _tar_archive(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        root = tarfile.TarInfo("workspace")
        root.type = tarfile.DIRTYPE
        tar.addfile(root)
        for name, content in files.items():
            info = tarfile.TarInfo(f"workspace/{name}")
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


def _tar_archive_without_workspace_root(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        for name, content in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    return buffer.getvalue()

"""Tests for the OpenCode container lifecycle: task ownership and ports."""

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
        container = Mock()
        container.id = f"container-{len(created)}"
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

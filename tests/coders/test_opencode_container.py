"""Tests for task ownership of OpenCode containers."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from snodo.coders.opencode_container import OpenCodeContainer


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

    first.start(Path(tmp_path / "one"), task_id="task-one")
    second.start(Path(tmp_path / "two"), task_id="task-two")

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

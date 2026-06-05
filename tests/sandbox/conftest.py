"""Shared fixtures for sandbox tests.

FILE: tests/sandbox/conftest.py
"""

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def docker_client_mock():
    """Return a pre-configured (mock_client, mock_container) tuple.

    mock_client.ping.return_value = True
    mock_container.wait.return_value = {"StatusCode": 0}
    mock_client.containers.run.return_value = mock_container
    """
    mock_client = MagicMock()
    mock_container = MagicMock()
    mock_container.id = "abc123def456"
    mock_container.wait.return_value = {"StatusCode": 0}
    mock_container.logs.side_effect = [
        b"task output\n",
        b"",
    ]
    mock_client.ping.return_value = True
    mock_client.containers.run.return_value = mock_container
    with patch("docker.from_env", return_value=mock_client):
        yield mock_client, mock_container

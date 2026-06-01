"""Tests for the agent command module.

FILE: tests/cli/test_agent_cmd.py

Unit tests for agent_command, _agent_list, _agent_memory, _agent_reset,
_agent_rotate, and _format_time in snodo/cli/commands/agent_cmd.py.
"""

from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import pytest

from snodo.cli.commands.agent_cmd import (
    agent_command,
    _agent_list,
    _agent_memory,
    _agent_reset,
    _agent_rotate,
    _format_time,
)
from snodo.infrastructure.memory import MemoryError as SnodoMemoryError


# === Fixtures ===

@pytest.fixture
def mock_manager():
    """Create a mock AgentMemoryManager."""
    return MagicMock()


# === agent_command Tests ===

class TestAgentCommand:
    @patch("snodo.infrastructure.memory.AgentMemoryManager")
    def test_manager_creation_failure(self, MockManager, capsys):
        """agent_command returns 1 when AgentMemoryManager raises."""
        MockManager.side_effect = RuntimeError("no config found")
        args = SimpleNamespace(agent_action="list")

        result = agent_command(args)

        assert result == 1
        captured = capsys.readouterr()
        assert "Error: no config found" in captured.err

    @patch("snodo.infrastructure.memory.AgentMemoryManager")
    def test_unknown_action(self, MockManager, capsys):
        """agent_command returns 1 for an unknown action."""
        MockManager.return_value = MagicMock()
        args = SimpleNamespace(agent_action="destroy")

        result = agent_command(args)

        assert result == 1
        captured = capsys.readouterr()
        assert "Unknown agent action" in captured.err

    @patch("snodo.infrastructure.memory.AgentMemoryManager")
    def test_routes_to_list(self, MockManager):
        """agent_command routes 'list' to _agent_list."""
        manager = MagicMock()
        manager.list_agents.return_value = []
        MockManager.return_value = manager
        args = SimpleNamespace(agent_action="list")

        result = agent_command(args)

        assert result == 0
        manager.list_agents.assert_called_once()

    @patch("snodo.infrastructure.memory.AgentMemoryManager")
    def test_routes_to_memory(self, MockManager):
        """agent_command routes 'memory' to _agent_memory."""
        manager = MagicMock()
        manager.get_agent.return_value = {
            "id": "agent-1",
            "thread_id": "t123456789abc",
            "project": "myproject",
            "mode": "producer",
            "task_count": 5,
            "created_at": 1700000000.0,
            "last_used_at": 1700001000.0,
        }
        manager.get_memory_summary.return_value = {
            "checkpoint_count": 3,
            "db_exists": True,
        }
        MockManager.return_value = manager
        args = SimpleNamespace(agent_action="memory", agent_id="agent-1")

        result = agent_command(args)

        assert result == 0
        manager.get_agent.assert_called_once_with("agent-1")

    @patch("snodo.infrastructure.memory.AgentMemoryManager")
    def test_routes_to_reset(self, MockManager):
        """agent_command routes 'reset' to _agent_reset."""
        manager = MagicMock()
        manager.reset_memory.return_value = {"thread_id": "new-thread-abc"}
        MockManager.return_value = manager
        args = SimpleNamespace(agent_action="reset", agent_id="agent-1")

        result = agent_command(args)

        assert result == 0
        manager.reset_memory.assert_called_once_with("agent-1")

    @patch("snodo.infrastructure.memory.AgentMemoryManager")
    def test_routes_to_rotate(self, MockManager):
        """agent_command routes 'rotate' to _agent_rotate."""
        manager = MagicMock()
        manager.rotate_thread.return_value = {"thread_id": "rotated-thread-xyz"}
        MockManager.return_value = manager
        args = SimpleNamespace(agent_action="rotate", agent_id="agent-1")

        result = agent_command(args)

        assert result == 0
        manager.rotate_thread.assert_called_once_with("agent-1")

    @patch("snodo.infrastructure.memory.AgentMemoryManager")
    def test_memory_error_caught(self, MockManager, capsys):
        """agent_command catches MemoryError from actions and returns 1."""
        manager = MagicMock()
        manager.list_agents.side_effect = SnodoMemoryError("memory corruption")
        MockManager.return_value = manager
        args = SimpleNamespace(agent_action="list")

        result = agent_command(args)

        assert result == 1
        captured = capsys.readouterr()
        assert "Error: memory corruption" in captured.err


# === _agent_list Tests ===

class TestAgentList:
    def test_empty_agents_list(self, mock_manager, capsys):
        """_agent_list prints message when no agents found."""
        mock_manager.list_agents.return_value = []

        result = _agent_list(mock_manager)

        assert result == 0
        captured = capsys.readouterr()
        assert "No agents found" in captured.out
        assert "created automatically" in captured.out

    def test_agents_listed_with_table(self, mock_manager, capsys):
        """_agent_list prints a formatted table of agents."""
        mock_manager.list_agents.return_value = [
            {
                "id": "agent-producer",
                "thread_id": "t123456789abcdef",
                "task_count": 10,
                "last_used_at": 1700000000.0,
            },
            {
                "id": "agent-reviewer",
                "thread_id": "taaabbbcccdddee",
                "task_count": 3,
                "last_used_at": None,
            },
        ]

        result = _agent_list(mock_manager)

        assert result == 0
        captured = capsys.readouterr()
        assert "agent-producer" in captured.out
        assert "agent-reviewer" in captured.out
        assert "t1234567..." in captured.out
        assert "ID" in captured.out
        assert "-" * 72 in captured.out

    def test_agents_with_zero_task_count(self, mock_manager, capsys):
        """_agent_list handles agents with no task_count key."""
        mock_manager.list_agents.return_value = [
            {
                "id": "agent-new",
                "thread_id": "t000000000000000",
                # No task_count key
            },
        ]

        result = _agent_list(mock_manager)

        assert result == 0
        captured = capsys.readouterr()
        assert "agent-new" in captured.out


# === _agent_memory Tests ===

class TestAgentMemory:
    def test_agent_not_found(self, mock_manager, capsys):
        """_agent_memory returns 1 when agent not found."""
        mock_manager.get_agent.return_value = None

        result = _agent_memory(mock_manager, "nonexistent-agent")

        assert result == 1
        captured = capsys.readouterr()
        assert "Error: Agent not found: nonexistent-agent" in captured.err

    def test_agent_found_full_output(self, mock_manager, capsys):
        """_agent_memory prints complete agent details and summary."""
        mock_manager.get_agent.return_value = {
            "id": "agent-1",
            "thread_id": "thread-abc-123",
            "project": "myproject",
            "mode": "producer",
            "task_count": 7,
            "created_at": 1700000000.0,
            "last_used_at": 1700005000.0,
        }
        mock_manager.get_memory_summary.return_value = {
            "checkpoint_count": 12,
            "db_exists": True,
        }

        result = _agent_memory(mock_manager, "agent-1")

        assert result == 0
        captured = capsys.readouterr()
        out = captured.out

        assert "Agent: agent-1" in out
        assert "Thread ID: thread-abc-123" in out
        assert "Project: myproject" in out
        assert "Mode: producer" in out
        assert "Tasks completed: 7" in out
        assert "Checkpoints: 12" in out
        assert "Database: exists" in out

    def test_agent_memory_db_not_created(self, mock_manager, capsys):
        """_agent_memory shows 'not created' when db_exists is False."""
        mock_manager.get_agent.return_value = {
            "id": "agent-2",
            "thread_id": "thread-xyz",
        }
        mock_manager.get_memory_summary.return_value = {
            "checkpoint_count": 0,
            "db_exists": False,
        }

        result = _agent_memory(mock_manager, "agent-2")

        assert result == 0
        captured = capsys.readouterr()
        assert "Database: not created" in captured.out

    def test_agent_memory_missing_optional_fields(self, mock_manager, capsys):
        """_agent_memory uses N/A for missing optional fields."""
        mock_manager.get_agent.return_value = {
            "id": "agent-3",
            "thread_id": "thread-minimal",
            # No project, mode, task_count, created_at, last_used_at
        }
        mock_manager.get_memory_summary.return_value = {
            "checkpoint_count": 0,
            "db_exists": False,
        }

        result = _agent_memory(mock_manager, "agent-3")

        assert result == 0
        captured = capsys.readouterr()
        out = captured.out

        assert "Project: N/A" in out
        assert "Mode: N/A" in out
        assert "Tasks completed: 0" in out


# === _agent_reset Tests ===

class TestAgentReset:
    def test_reset_prints_confirmation(self, mock_manager, capsys):
        """_agent_reset prints success message with new thread ID."""
        mock_manager.reset_memory.return_value = {"thread_id": "new-thread-999"}

        result = _agent_reset(mock_manager, "agent-1")

        assert result == 0
        captured = capsys.readouterr()
        assert "Agent agent-1 memory cleared." in captured.out
        assert "New thread ID: new-thread-999" in captured.out

    def test_reset_calls_manager(self, mock_manager):
        """_agent_reset calls manager.reset_memory with correct agent_id."""
        mock_manager.reset_memory.return_value = {"thread_id": "t-new"}

        _agent_reset(mock_manager, "my-agent")

        mock_manager.reset_memory.assert_called_once_with("my-agent")


# === _agent_rotate Tests ===

class TestAgentRotate:
    def test_rotate_prints_confirmation(self, mock_manager, capsys):
        """_agent_rotate prints success message with new thread ID."""
        mock_manager.rotate_thread.return_value = {"thread_id": "rotated-thread-abc"}

        result = _agent_rotate(mock_manager, "agent-2")

        assert result == 0
        captured = capsys.readouterr()
        assert "Agent agent-2 thread rotated." in captured.out
        assert "New thread ID: rotated-thread-abc" in captured.out

    def test_rotate_calls_manager(self, mock_manager):
        """_agent_rotate calls manager.rotate_thread with correct agent_id."""
        mock_manager.rotate_thread.return_value = {"thread_id": "t-rotated"}

        _agent_rotate(mock_manager, "some-agent")

        mock_manager.rotate_thread.assert_called_once_with("some-agent")


# === _format_time Tests ===

class TestFormatTime:
    def test_none_returns_na(self):
        """_format_time returns 'N/A' for None."""
        assert _format_time(None) == "N/A"

    def test_zero_returns_na(self):
        """_format_time returns 'N/A' for 0 (falsy)."""
        assert _format_time(0) == "N/A"

    def test_empty_string_returns_na(self):
        """_format_time returns 'N/A' for empty string."""
        assert _format_time("") == "N/A"

    def test_valid_timestamp(self):
        """_format_time formats a valid Unix timestamp."""
        ts = 1700000000.0
        result = _format_time(ts)
        # Should produce a date string in YYYY-MM-DD HH:MM:SS format
        assert "2023" in result  # 1700000000 is Nov 2023
        assert len(result) == 19  # "YYYY-MM-DD HH:MM:SS"

    def test_invalid_type_returns_na(self):
        """_format_time returns 'N/A' for non-numeric types that pass truthiness."""
        assert _format_time("not-a-timestamp") == "N/A"

    def test_negative_timestamp_returns_na(self):
        """_format_time returns 'N/A' for negative values that cause ValueError/OSError."""
        # Very large negative values may cause ValueError or OSError on some platforms
        result = _format_time(-1e18)
        assert result == "N/A"

    def test_list_returns_na(self):
        """_format_time returns 'N/A' for unexpected types like list."""
        assert _format_time([1, 2, 3]) == "N/A"

    def test_dict_returns_na(self):
        """_format_time returns 'N/A' for dict input."""
        assert _format_time({"ts": 123}) == "N/A"

    def test_false_returns_na(self):
        """_format_time returns 'N/A' for False (falsy)."""
        assert _format_time(False) == "N/A"

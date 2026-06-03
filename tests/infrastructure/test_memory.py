"""Tests for agent memory management.

FILE: tests/infrastructure/test_memory.py (Task 5.2)

Unit tests for AgentMemoryManager, CLI integration tests,
and end-to-end checkpointing test.
"""

import os
import shutil
import sqlite3
import subprocess
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from snodo.infrastructure.memory import AgentMemoryManager, MemoryError


# === Fixtures ===

@pytest.fixture
def temp_home():
    """Create a temporary home directory for agent storage."""
    temp_dir = tempfile.mkdtemp()
    yield temp_dir
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def manager(temp_home):
    """Create an AgentMemoryManager with isolated temp storage."""
    return AgentMemoryManager(home_dir=temp_home)


@pytest.fixture
def temp_project():
    """Create a temporary project with .snodo/ for CLI tests."""
    temp_dir = tempfile.mkdtemp()
    snodo_dir = Path(temp_dir) / ".snodo"
    snodo_dir.mkdir()

    # Write a minimal protocol file
    protocol_file = snodo_dir / "protocol.yml"
    protocol_file.write_text(
        'protocol_id: "test"\n'
        'name: "Test Protocol"\n'
        'version: "1.0.0"\n'
        'modes:\n'
        '  - mode_id: "producer"\n'
        '    name: "Producer"\n'
        '    tools: ["edit"]\n'
        '    validators: ["security"]\n'
        '    transitions: {}\n'
        'validators:\n'
        '  - validator_id: "security"\n'
        '    validator_type: "security"\n'
        '    evaluation_phase: "pre_execute"\n'
        '    criteria: ["check"]\n'
        'disagreement_policy: "unanimous"\n'
        'initial_mode: "producer"\n'
        'global_constraints: []\n'
    )

    original_cwd = Path.cwd()
    try:
        os.chdir(temp_dir)
        yield Path(temp_dir)
    finally:
        os.chdir(original_cwd)
        shutil.rmtree(temp_dir, ignore_errors=True)


# === AgentMemoryManager Init Tests ===

class TestManagerInit:
    def test_creates_home_dir(self, temp_home):
        """Manager creates ~/.snodo directory."""
        home = os.path.join(temp_home, "subdir")
        mgr = AgentMemoryManager(home_dir=home)
        assert Path(home).is_dir()

    def test_db_path(self, manager, temp_home):
        """Database path points to checkpoints.db."""
        assert manager.db_path == Path(temp_home) / "checkpoints.db"

    def test_agents_path(self, manager, temp_home):
        """Agents registry points to agents.json."""
        assert manager.agents_path == Path(temp_home) / "agents.json"


# === Agent Registry Tests ===

class TestAgentRegistry:
    def test_get_or_create_new(self, manager):
        """get_or_create_agent creates a new agent entry."""
        agent = manager.get_or_create_agent("myproject", "producer")
        assert "thread_id" in agent
        assert agent["project"] == "myproject"
        assert agent["mode"] == "producer"
        assert agent["task_count"] == 0

    def test_get_or_create_existing(self, manager):
        """get_or_create_agent returns existing agent on second call."""
        agent1 = manager.get_or_create_agent("proj", "producer")
        agent2 = manager.get_or_create_agent("proj", "producer")
        assert agent1["thread_id"] == agent2["thread_id"]

    def test_different_modes_different_agents(self, manager):
        """Different modes create different agents."""
        a1 = manager.get_or_create_agent("proj", "producer")
        a2 = manager.get_or_create_agent("proj", "reviewer")
        assert a1["thread_id"] != a2["thread_id"]

    def test_different_projects_different_agents(self, manager):
        """Different projects create different agents."""
        a1 = manager.get_or_create_agent("proj_a", "producer")
        a2 = manager.get_or_create_agent("proj_b", "producer")
        assert a1["thread_id"] != a2["thread_id"]

    def test_record_task(self, manager):
        """record_task increments task count and updates last_used_at."""
        manager.get_or_create_agent("proj", "producer")
        manager.record_task("proj", "producer")
        agent = manager.get_agent("proj:producer")
        assert agent["task_count"] == 1
        assert "last_used_at" in agent

    def test_record_task_multiple(self, manager):
        """Multiple record_task calls accumulate."""
        manager.get_or_create_agent("proj", "producer")
        manager.record_task("proj", "producer")
        manager.record_task("proj", "producer")
        manager.record_task("proj", "producer")
        agent = manager.get_agent("proj:producer")
        assert agent["task_count"] == 3


# === List Agents Tests ===

class TestListAgents:
    def test_list_empty(self, manager):
        """list_agents returns empty list with no agents."""
        assert manager.list_agents() == []

    def test_list_returns_all(self, manager):
        """list_agents returns all registered agents."""
        manager.get_or_create_agent("a", "producer")
        manager.get_or_create_agent("b", "reviewer")
        agents = manager.list_agents()
        assert len(agents) == 2
        ids = [a["id"] for a in agents]
        assert "a:producer" in ids
        assert "b:reviewer" in ids

    def test_list_sorted_newest_first(self, manager):
        """list_agents returns newest agents first."""
        manager.get_or_create_agent("old", "producer")
        time.sleep(0.01)
        manager.get_or_create_agent("new", "producer")
        agents = manager.list_agents()
        assert agents[0]["id"] == "new:producer"


# === Get Agent Tests ===

class TestGetAgent:
    def test_get_existing(self, manager):
        """get_agent returns agent dict."""
        manager.get_or_create_agent("proj", "producer")
        agent = manager.get_agent("proj:producer")
        assert agent is not None
        assert agent["id"] == "proj:producer"

    def test_get_nonexistent(self, manager):
        """get_agent returns None for unknown agent."""
        assert manager.get_agent("nonexistent:mode") is None


# === Memory Summary Tests ===

class TestMemorySummary:
    def test_summary_no_db(self, manager):
        """Memory summary works even when DB doesn't exist yet."""
        manager.get_or_create_agent("proj", "producer")
        summary = manager.get_memory_summary("proj:producer")
        assert summary["checkpoint_count"] == 0
        assert summary["agent_id"] == "proj:producer"

    def test_summary_nonexistent_agent(self, manager):
        """Memory summary raises for unknown agent."""
        with pytest.raises(MemoryError, match="Agent not found"):
            manager.get_memory_summary("nonexistent:mode")

    def test_summary_with_db(self, manager):
        """Memory summary queries checkpoint count when DB exists."""
        agent = manager.get_or_create_agent("proj", "producer")
        # Create a minimal checkpoints table
        conn = sqlite3.connect(str(manager.db_path))
        conn.execute(
            "CREATE TABLE IF NOT EXISTS checkpoints "
            "(thread_id TEXT, checkpoint_ns TEXT, checkpoint_id TEXT, "
            "parent_checkpoint_id TEXT, type TEXT, checkpoint BLOB, metadata BLOB)"
        )
        conn.execute(
            "INSERT INTO checkpoints VALUES (?, '', 'cp1', '', '', '', '')",
            (agent["thread_id"],),
        )
        conn.execute(
            "INSERT INTO checkpoints VALUES (?, '', 'cp2', '', '', '', '')",
            (agent["thread_id"],),
        )
        conn.commit()
        conn.close()

        summary = manager.get_memory_summary("proj:producer")
        assert summary["checkpoint_count"] == 2
        assert summary["db_exists"] is True


# === Reset Memory Tests ===

class TestResetMemory:
    def test_reset_clears_thread(self, manager):
        """reset_memory assigns a new thread_id."""
        agent = manager.get_or_create_agent("proj", "producer")
        old_thread = agent["thread_id"]

        result = manager.reset_memory("proj:producer")
        assert result["thread_id"] != old_thread

    def test_reset_clears_task_count(self, manager):
        """reset_memory resets task count to 0."""
        manager.get_or_create_agent("proj", "producer")
        manager.record_task("proj", "producer")
        manager.record_task("proj", "producer")

        result = manager.reset_memory("proj:producer")
        assert result["task_count"] == 0

    def test_reset_deletes_checkpoints(self, manager):
        """reset_memory removes checkpoints from database."""
        agent = manager.get_or_create_agent("proj", "producer")
        thread_id = agent["thread_id"]

        # Create checkpoints
        conn = sqlite3.connect(str(manager.db_path))
        conn.execute(
            "CREATE TABLE IF NOT EXISTS checkpoints "
            "(thread_id TEXT, checkpoint_ns TEXT, checkpoint_id TEXT, "
            "parent_checkpoint_id TEXT, type TEXT, checkpoint BLOB, metadata BLOB)"
        )
        conn.execute(
            "INSERT INTO checkpoints VALUES (?, '', 'cp1', '', '', '', '')",
            (thread_id,),
        )
        conn.commit()
        conn.close()

        manager.reset_memory("proj:producer")

        # Verify checkpoints are deleted
        conn = sqlite3.connect(str(manager.db_path))
        cursor = conn.execute(
            "SELECT COUNT(*) FROM checkpoints WHERE thread_id = ?",
            (thread_id,),
        )
        assert cursor.fetchone()[0] == 0
        conn.close()

    def test_reset_nonexistent_raises(self, manager):
        """reset_memory raises for unknown agent."""
        with pytest.raises(MemoryError, match="Agent not found"):
            manager.reset_memory("nonexistent:mode")


# === Rotate Thread Tests ===

class TestRotateThread:
    def test_rotate_changes_thread(self, manager):
        """rotate_thread assigns a new thread_id."""
        agent = manager.get_or_create_agent("proj", "producer")
        old_thread = agent["thread_id"]

        result = manager.rotate_thread("proj:producer")
        assert result["thread_id"] != old_thread

    def test_rotate_preserves_task_count(self, manager):
        """rotate_thread keeps task_count (unlike reset)."""
        manager.get_or_create_agent("proj", "producer")
        manager.record_task("proj", "producer")
        manager.record_task("proj", "producer")

        # Read current task_count
        agent_before = manager.get_agent("proj:producer")
        manager.rotate_thread("proj:producer")
        agent_after = manager.get_agent("proj:producer")
        assert agent_after["task_count"] == agent_before["task_count"]

    def test_rotate_nonexistent_raises(self, manager):
        """rotate_thread raises for unknown agent."""
        with pytest.raises(MemoryError, match="Agent not found"):
            manager.rotate_thread("nonexistent:mode")


# === Checkpointer Tests ===

class TestCheckpointer:
    def test_get_checkpointer_creates_db(self, manager):
        """get_checkpointer creates the SQLite database file."""
        saver = manager.get_checkpointer()
        assert manager.db_path.exists()
        saver.conn.close()

    def test_checkpointer_is_sqlitesaver(self, manager):
        """get_checkpointer returns a SqliteSaver instance."""
        from langgraph.checkpoint.sqlite import SqliteSaver
        saver = manager.get_checkpointer()
        assert isinstance(saver, SqliteSaver)
        saver.conn.close()


# === CLI Integration Tests ===

class TestAgentCLI:
    def test_agent_list_via_main(self, temp_project):
        """snodo agent list works via main()."""
        from snodo.cli.main import main
        result = main(["agent", "list"])
        assert result == 0

    def test_agent_memory_missing_id(self, temp_project):
        """snodo agent memory with unknown ID returns error."""
        from snodo.cli.main import main
        result = main(["agent", "memory", "nonexistent:mode"])
        assert result == 1

    def test_agent_reset_missing_id(self, temp_project):
        """snodo agent reset with unknown ID returns error."""
        from snodo.cli.main import main
        result = main(["agent", "reset", "nonexistent:mode"])
        assert result == 1

    def test_agent_rotate_missing_id(self, temp_project):
        """snodo agent rotate with unknown ID returns error."""
        from snodo.cli.main import main
        result = main(["agent", "rotate", "nonexistent:mode"])
        assert result == 1

    def test_agent_list_shows_agents(self, temp_project, capsys):
        """snodo agent list displays registered agents."""
        from snodo.cli.main import main

        # Create an agent first
        mgr = AgentMemoryManager()
        mgr.get_or_create_agent("testproject", "producer")

        result = main(["agent", "list"])
        assert result == 0
        captured = capsys.readouterr()
        assert "testproject:producer" in captured.out


# === End-to-End Checkpointer Test ===

class TestEndToEnd:

    @staticmethod
    def _init_git_repo(project_dir):
        """Initialize a git repo in the project directory."""
        subprocess.run(["git", "init"], cwd=str(project_dir), capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"],
                       cwd=str(project_dir), capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"],
                       cwd=str(project_dir), capture_output=True)
        (project_dir / "README.md").write_text("test")
        subprocess.run(["git", "add", "."], cwd=str(project_dir), capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"],
                       cwd=str(project_dir), capture_output=True)

    def test_checkpointer_with_graph(self, temp_project):
        """Graph execution with checkpointer persists state."""
        from snodo.cli.commands import load_protocol
        from snodo.engine.loop import build_protocol_graph
        from snodo.core.interfaces import ValidatorResult

        def _all_pass(task, validators, shell_mcp, current_mode="", **kwargs):
            return [
                ValidatorResult(validator_id=v.validator_id, severity="pass",
                                justification="ok")
                for v in validators
            ]

        self._init_git_repo(temp_project)

        protocol = load_protocol(Path(".snodo/protocol.yml"))
        assert protocol is not None

        temp_home = tempfile.mkdtemp()
        try:
            mgr = AgentMemoryManager(home_dir=temp_home)
            agent = mgr.get_or_create_agent("test", "producer")
            checkpointer = mgr.get_checkpointer()
            thread_id = agent["thread_id"]

            graph = build_protocol_graph(
                protocol,
                project_root=str(temp_project),
                use_mock_coder=True,
                checkpointer=checkpointer,
                validator_fn=_all_pass,
            )
            compiled = graph.compile(checkpointer=checkpointer)

            initial_state = {
                "task": {"id": "test_001", "spec": "test task"},
                "current_mode": "producer",
                "iteration": 0,
                "stage": "governance",
                "validation_results": [],
                "validation_token": None,
                "artifacts": [],
                "constraints_passed": True,
                "constraint_violations": [],
                "policy_decision": None,
                "is_complete": False,
                "is_blocked": False,
                "metadata": {},
                "messages": [],
            }

            config = {"configurable": {"thread_id": thread_id}}
            final_state = None
            for state in compiled.stream(initial_state, config=config):
                if isinstance(state, dict):
                    node_state = next(iter(state.values()), {})
                    if isinstance(node_state, dict) and "stage" in node_state:
                        final_state = node_state

            # Verify execution completed
            assert final_state is not None
            assert final_state.get("stage") == "complete"

            # Verify messages were accumulated
            messages = final_state.get("messages", [])
            assert len(messages) >= 2  # At least task + completion messages

            # Verify checkpoints were written
            summary = mgr.get_memory_summary("test:producer")
            assert summary["checkpoint_count"] > 0

            checkpointer.conn.close()
        finally:
            shutil.rmtree(temp_home, ignore_errors=True)

    def test_messages_contain_task_info(self, temp_project):
        """Messages in state contain task and execution info."""
        from snodo.cli.commands import load_protocol
        from snodo.engine.loop import build_protocol_graph
        from snodo.core.interfaces import ValidatorResult

        def _all_pass(task, validators, shell_mcp, current_mode="", **kwargs):
            return [
                ValidatorResult(validator_id=v.validator_id, severity="pass",
                                justification="ok")
                for v in validators
            ]

        self._init_git_repo(temp_project)

        protocol = load_protocol(Path(".snodo/protocol.yml"))
        assert protocol is not None

        graph = build_protocol_graph(
            protocol,
            project_root=str(temp_project),
            use_mock_coder=True,
            validator_fn=_all_pass,
        )
        compiled = graph.compile()

        initial_state = {
            "task": {"id": "msg_test", "spec": "implement feature X"},
            "current_mode": "producer",
            "iteration": 0,
            "stage": "governance",
            "validation_results": [],
            "validation_token": None,
            "artifacts": [],
            "constraints_passed": True,
            "constraint_violations": [],
            "policy_decision": None,
            "is_complete": False,
            "is_blocked": False,
            "metadata": {},
            "messages": [],
        }

        final_state = None
        for state in compiled.stream(initial_state):
            if isinstance(state, dict):
                node_state = next(iter(state.values()), {})
                if isinstance(node_state, dict) and "stage" in node_state:
                    final_state = node_state

        messages = final_state.get("messages", [])
        # Should have user message (task) and assistant messages (execution, completion)
        user_msgs = [m for m in messages if m.get("role") == "user"]
        asst_msgs = [m for m in messages if m.get("role") == "assistant"]
        assert len(user_msgs) >= 1
        assert len(asst_msgs) >= 1
        assert "implement feature X" in user_msgs[0]["content"]


# === Task 6.7: Summary Model Tests ===

class TestSummaryModel:
    def test_create_summary_model_returns_none_no_keys(self):
        """create_summary_model returns None when no API keys configured."""
        from snodo.infrastructure.memory import create_summary_model
        with patch("snodo.cli.config.ConfigManager") as mock_cm:
            mock_cm.return_value.get_key.return_value = None
            model = create_summary_model()
        assert model is None

    def test_summary_field_in_end_to_end(self, temp_project):
        """End-to-end graph state includes summary field."""
        from snodo.cli.commands import load_protocol
        from snodo.engine.loop import build_protocol_graph
        from snodo.core.interfaces import ValidatorResult

        def _all_pass(task, validators, shell_mcp, current_mode="", **kwargs):
            return [
                ValidatorResult(validator_id=v.validator_id, severity="pass",
                                justification="ok")
                for v in validators
            ]

        self._init_git_repo(temp_project)

        protocol = load_protocol(Path(".snodo/protocol.yml"))
        assert protocol is not None

        graph = build_protocol_graph(
            protocol,
            project_root=str(temp_project),
            use_mock_coder=True,
            validator_fn=_all_pass,
        )
        compiled = graph.compile()

        initial_state = {
            "task": {"id": "summary_test", "spec": "test summary"},
            "current_mode": "producer",
            "iteration": 0,
            "stage": "governance",
            "validation_results": [],
            "validation_token": None,
            "artifacts": [],
            "constraints_passed": True,
            "constraint_violations": [],
            "policy_decision": None,
            "is_complete": False,
            "is_blocked": False,
            "metadata": {},
            "messages": [],
            "summary": "",
        }

        final_state = None
        for state in compiled.stream(initial_state):
            if isinstance(state, dict):
                node_state = next(iter(state.values()), {})
                if isinstance(node_state, dict) and "stage" in node_state:
                    final_state = node_state

        assert final_state is not None
        assert "summary" in final_state
        assert "messages" in final_state

    @staticmethod
    def _init_git_repo(project_dir):
        """Initialize a git repo in the project directory."""
        subprocess.run(["git", "init"], cwd=str(project_dir), capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"],
                       cwd=str(project_dir), capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"],
                       cwd=str(project_dir), capture_output=True)
        (project_dir / "README.md").write_text("test")
        subprocess.run(["git", "add", "."], cwd=str(project_dir), capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"],
                       cwd=str(project_dir), capture_output=True)

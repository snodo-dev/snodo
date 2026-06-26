"""Agent memory management via LangGraph SqliteSaver.

FILE: snodo/infrastructure/memory.py (Task 5.2)

Manages persistent agent memory:
- SqliteSaver for LangGraph checkpointing (~/.snodo/checkpoints.db)
- Agent registry (~/.snodo/agents.json) with thread IDs
- Per-agent isolation via unique thread_id
"""

import json
import logging
import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import List, Optional

from langgraph.checkpoint.sqlite import SqliteSaver

from snodo.infrastructure.paths import resolve_home

_logger = logging.getLogger(__name__)


class MemoryError(Exception):
    """Agent memory error."""


class AgentMemoryManager:
    """Manages agent memory and thread isolation.

    Storage:
    - ~/.snodo/checkpoints.db — LangGraph checkpoint database
    - ~/.snodo/agents.json — agent registry with thread IDs
    """

    def __init__(self, home_dir: Optional[str] = None):
        """Initialize memory manager.

        Args:
            home_dir: Override home directory (default: ~/.snodo)
        """
        self.snodo_home = Path(home_dir) if home_dir else resolve_home()
        self.snodo_home.mkdir(parents=True, exist_ok=True)
        self.db_path = self.snodo_home / "checkpoints.db"
        self.agents_path = self.snodo_home / "agents.json"

    def get_checkpointer(self) -> SqliteSaver:
        """Create a SqliteSaver connected to the checkpoints database.

        Returns:
            SqliteSaver instance. Caller is responsible for closing
            the underlying connection when done.
        """
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        saver = SqliteSaver(conn)
        saver.setup()
        return saver

    def _load_registry(self) -> dict:
        """Load agent registry from disk.

        Returns an empty registry if the file is missing or corrupted,
        logging a warning in the latter case so the operator can fix it.
        """
        if not self.agents_path.exists():
            return {"agents": {}}
        try:
            with open(self.agents_path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            _logger.warning(
                "Corrupt agents.json at %s — returning empty registry (%s)",
                self.agents_path, e,
            )
            return {"agents": {}}

    def _save_registry(self, registry: dict) -> None:
        """Atomically save agent registry."""
        tmp_path = self.agents_path.with_suffix(".json.tmp")
        with open(tmp_path, "w") as f:
            json.dump(registry, f, indent=2)
        os.rename(str(tmp_path), str(self.agents_path))

    def get_or_create_agent(self, project: str, mode: str) -> dict:
        """Get or create an agent entry, returning its config.

        Args:
            project: Project name (e.g., directory basename)
            mode: Protocol mode (e.g., "producer", "reviewer")

        Returns:
            Agent dict with thread_id, project, mode, etc.
        """
        agent_id = f"{project}:{mode}"
        registry = self._load_registry()
        agents = registry.setdefault("agents", {})

        if agent_id not in agents:
            agents[agent_id] = {
                "thread_id": str(uuid.uuid4()),
                "project": project,
                "mode": mode,
                "created_at": time.time(),
                "task_count": 0,
            }
            self._save_registry(registry)

        return agents[agent_id]

    def record_task(self, project: str, mode: str) -> None:
        """Increment task count for an agent.

        Args:
            project: Project name
            mode: Protocol mode
        """
        agent_id = f"{project}:{mode}"
        registry = self._load_registry()
        agents = registry.get("agents", {})
        if agent_id in agents:
            agents[agent_id]["task_count"] = agents[agent_id].get("task_count", 0) + 1
            agents[agent_id]["last_used_at"] = time.time()
            self._save_registry(registry)

    def list_agents(self) -> List[dict]:
        """List all registered agents.

        Returns:
            List of agent dicts with id, thread_id, project, mode, etc.
        """
        registry = self._load_registry()
        agents = registry.get("agents", {})
        result = []
        for agent_id, info in agents.items():
            result.append({"id": agent_id, **info})
        result.sort(key=lambda a: a.get("created_at", 0), reverse=True)
        return result

    def get_agent(self, agent_id: str) -> Optional[dict]:
        """Get a single agent's details.

        Args:
            agent_id: Agent identifier (project:mode)

        Returns:
            Agent dict or None if not found
        """
        registry = self._load_registry()
        agents = registry.get("agents", {})
        info = agents.get(agent_id)
        if info is None:
            return None
        return {"id": agent_id, **info}

    def get_memory_summary(self, agent_id: str) -> dict:
        """Get memory summary for an agent.

        Queries the checkpoint database for checkpoint count and
        approximate size for this agent's thread.

        Args:
            agent_id: Agent identifier (project:mode)

        Returns:
            Dict with checkpoint_count, thread_id, approximate_size_bytes
        """
        agent = self.get_agent(agent_id)
        if agent is None:
            raise MemoryError(f"Agent not found: {agent_id}")

        thread_id = agent["thread_id"]
        summary = {
            "agent_id": agent_id,
            "thread_id": thread_id,
            "checkpoint_count": 0,
            "db_exists": self.db_path.exists(),
        }

        if not self.db_path.exists():
            return summary

        conn = sqlite3.connect(str(self.db_path))
        try:
            cursor = conn.execute(
                "SELECT COUNT(*) FROM checkpoints WHERE thread_id = ?",
                (thread_id,),
            )
            summary["checkpoint_count"] = cursor.fetchone()[0]
        except sqlite3.OperationalError:
            # Table may not exist yet
            pass
        finally:
            conn.close()

        return summary

    def reset_memory(self, agent_id: str) -> dict:
        """Clear all checkpoints for an agent and assign a new thread_id.

        Args:
            agent_id: Agent identifier (project:mode)

        Returns:
            Updated agent dict with new thread_id

        Raises:
            MemoryError: If agent not found
        """
        registry = self._load_registry()
        agents = registry.get("agents", {})

        if agent_id not in agents:
            raise MemoryError(f"Agent not found: {agent_id}")

        old_thread_id = agents[agent_id]["thread_id"]

        # Delete checkpoints for old thread
        self._delete_thread_checkpoints(old_thread_id)

        # Assign new thread_id
        agents[agent_id]["thread_id"] = str(uuid.uuid4())
        agents[agent_id]["task_count"] = 0
        self._save_registry(registry)

        return {"id": agent_id, **agents[agent_id]}

    def rotate_thread(self, agent_id: str) -> dict:
        """Rotate thread_id for an agent (keeps old checkpoints).

        Useful when you want a fresh context without deleting history.

        Args:
            agent_id: Agent identifier (project:mode)

        Returns:
            Updated agent dict with new thread_id

        Raises:
            MemoryError: If agent not found
        """
        registry = self._load_registry()
        agents = registry.get("agents", {})

        if agent_id not in agents:
            raise MemoryError(f"Agent not found: {agent_id}")

        agents[agent_id]["thread_id"] = str(uuid.uuid4())
        self._save_registry(registry)

        return {"id": agent_id, **agents[agent_id]}

    def _delete_thread_checkpoints(self, thread_id: str) -> None:
        """Delete all checkpoints for a thread from the database."""
        if not self.db_path.exists():
            return

        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute("DELETE FROM checkpoints WHERE thread_id = ?", (thread_id,))
            # Also clean writes table if it exists
            try:
                conn.execute("DELETE FROM checkpoint_writes WHERE thread_id = ?", (thread_id,))
            except sqlite3.OperationalError:
                pass
            conn.commit()
        except sqlite3.OperationalError:
            pass  # Table may not exist yet
        finally:
            conn.close()


def create_summary_model():
    """Create a chat model for context summarization, using the user's configured model.

    Tries the user's configured model from config.yml first, falling
    back to DEFAULT_MODEL.  Returns None if no API keys are configured
    for the resolved provider.
    """
    from snodo.config import ConfigManager
    from snodo.infrastructure.config import DEFAULT_MODEL

    mgr = ConfigManager()

    model = mgr.get_model() or DEFAULT_MODEL
    provider = ConfigManager._provider_for_model(model)
    key = mgr.get_key(provider) if provider else None

    if provider == "openai" and key:
        try:
            from langchain_openai import ChatOpenAI
            return ChatOpenAI(model=model, api_key=key)
        except Exception:
            pass

    if provider == "anthropic" and key:
        try:
            from langchain_anthropic import ChatAnthropic
            return ChatAnthropic(model=model, api_key=key)
        except Exception:
            pass

    return None

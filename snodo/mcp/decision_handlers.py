"""Decision proposal tool handlers for the MCP server.

FILE: snodo/mcp/decision_handlers.py

Mirrors the ModelToolHandler / JobToolHandler pattern.  Stores unsigned
proposals in session checkpoint decisions so the human CLI path
('snodo authorize') can review and RS256-sign them.
"""

from datetime import datetime, timezone
from typing import Any, Dict

from snodo.infrastructure.state import read_state
from snodo.infrastructure.session import SessionManager


class DecisionToolHandler:
    """Handles propose_adjudicate and propose_set_model tool calls."""

    def __init__(self, project_root: str):
        self.project_root = project_root

    def _get_active_session(self) -> Any:
        project_root = self.project_root
        state = read_state(project_root)
        mode = state.current_mode or "producer"
        mgr = SessionManager()
        session = mgr.get_active_session(mode, project_root)
        if session is None:
            from snodo.mcp.server import MCPError
            raise MCPError(
                f"No active session for mode={mode}. "
                "Start a session first (run a task in this mode)."
            )
        return session, mgr

    def handle_propose_adjudicate(self, arguments: Dict[str, Any]) -> dict:
        """Store an adjudication proposal in pending_decisions.

        Args:
            arguments: task_id, validator_id, decision, justification.

        Returns:
            ``{"status": "pending", "task_id": ..., "instruction": "..."}``
        """
        task_id = arguments.get("task_id", "")
        validator_id = arguments.get("validator_id", "")
        decision = arguments.get("decision", "")
        justification = arguments.get("justification", "")

        from snodo.mcp.server import MCPError

        if not task_id:
            raise MCPError("propose_adjudicate requires task_id")
        if not validator_id:
            raise MCPError("propose_adjudicate requires validator_id")
        if decision not in ("proceed", "halt"):
            raise MCPError("decision must be 'proceed' or 'halt'")

        session, mgr = self._get_active_session()

        pending = session.checkpoint.decisions.get("pending_decisions", {})
        if not isinstance(pending, dict):
            pending = {}

        proposal = {
            "type": "adjudicate",
            "validator_id": validator_id,
            "decision": decision,
            "justification": justification,
            "proposed_by": "agent",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        pending[task_id] = proposal
        mgr.update_decision(session.session_id, "pending_decisions", pending)

        return {
            "status": "pending",
            "task_id": task_id,
            "instruction": f"Run: snodo authorize {task_id}",
            "proposal": proposal,
        }

    def handle_propose_set_model(self, arguments: Dict[str, Any]) -> dict:
        """Store a set_model proposal in pending_decisions.

        Args:
            arguments: task_id, proposed_model, scope, justification.

        Returns:
            ``{"status": "pending", "task_id": ..., "instruction": "..."}``
        """
        task_id = arguments.get("task_id", "")
        proposed_model = arguments.get("proposed_model", "")
        scope = arguments.get("scope", "")
        justification = arguments.get("justification", "")

        from snodo.mcp.server import MCPError

        if not task_id:
            raise MCPError("propose_set_model requires task_id")
        if not proposed_model:
            raise MCPError("propose_set_model requires proposed_model")
        if not scope:
            raise MCPError("propose_set_model requires scope")

        session, mgr = self._get_active_session()

        pending = session.checkpoint.decisions.get("pending_decisions", {})
        if not isinstance(pending, dict):
            pending = {}

        proposal = {
            "type": "set_model",
            "proposed_model": proposed_model,
            "scope": scope,
            "justification": justification,
            "proposed_by": "agent",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        pending[task_id] = proposal
        mgr.update_decision(session.session_id, "pending_decisions", pending)

        return {
            "status": "pending",
            "task_id": task_id,
            "instruction": f"Run: snodo authorize {task_id}",
            "proposal": proposal,
        }

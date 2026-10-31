"""MCP tools for recording and reading local intent records."""

from typing import Any

from snodo.infrastructure.intent_store import IntentError, IntentStore
from snodo.infrastructure.intent_trace import plans_for_intent


class IntentToolHandler:
    """Expose the project-local intent store over MCP."""

    def __init__(self, project_root: str, audit_log=None):
        self.store = IntentStore(project_root, audit_log=audit_log)

    @staticmethod
    def _error(exc: IntentError):
        from snodo.mcp.server import MCPError
        raise MCPError(str(exc)) from exc

    def handle_record_intent(self, arguments: dict[str, Any]) -> dict:
        try:
            return self.store.record(arguments.get("text"), issue=arguments.get("issue"),
                                     supersedes=arguments.get("supersedes"))
        except IntentError as exc:
            self._error(exc)

    def handle_get_intent(self, arguments: dict[str, Any]) -> dict:
        try:
            intent = self.store.get(arguments.get("intent_id"))
            intent["plans"] = plans_for_intent(self.store.project_root, intent["id"])
            return intent
        except IntentError as exc:
            self._error(exc)

    def handle_list_intents(self, arguments: dict[str, Any]) -> list[dict]:
        records = self.store.list()
        limit = arguments.get("limit")
        if limit is not None:
            if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
                from snodo.mcp.server import MCPError
                raise MCPError("list_intents limit must be a positive integer")
            records = records[:limit]
        return records

    def tool_handlers(self) -> dict:
        return {"record_intent": self.handle_record_intent, "get_intent": self.handle_get_intent,
                "list_intents": self.handle_list_intents}

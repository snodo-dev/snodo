"""Model discovery tool handlers for the MCP server.

FILE: snodo/mcp/model_handlers.py

Mirrors the JobToolHandler pattern — extracted from mcp/server.py to
isolate model discovery and resolution tool handling.
"""

from typing import Any, Dict

from snodo.infrastructure.config import DEFAULT_PROVIDER_CATALOG
from snodo.infrastructure.model_discovery import discover_models
from snodo.infrastructure.model_resolver import resolve_model


class ModelToolHandler:
    """Handles list_models and resolve_model tool calls."""

    def _get_models(self, provider: str = "") -> list:
        """Discover models, optionally filtered to one provider."""
        providers = dict(DEFAULT_PROVIDER_CATALOG)
        if provider:
            providers = {provider: providers[provider]} if provider in providers else {}

        models = discover_models(providers)
        if provider:
            models = [m for m in models if m.provider == provider]
        return [m.model_dump() for m in models]

    def handle_list_models(self, arguments: Dict[str, Any]) -> dict:
        """List available models across configured providers.

        Args:
            arguments: Optional ``provider`` filter.

        Returns:
            ``{"models": [...]}``
        """
        provider = arguments.get("provider", "") or ""
        models = self._get_models(provider)
        return {"models": models}

    def handle_resolve_model(self, arguments: Dict[str, Any]) -> dict:
        """Resolve a model query to a concrete model.

        Args:
            arguments: Required ``query`` string.  Optional ``index``
                       integer for disambiguating when multiple matches
                       exist.

        Returns:
            Exact match, or ambiguous candidates with hint, or not_found.
        """
        from snodo.mcp.server import MCPError

        query = arguments.get("query", "")
        if not query:
            raise MCPError("resolve_model requires query")

        providers = dict(DEFAULT_PROVIDER_CATALOG)
        models = discover_models(providers)
        result = resolve_model(query, models)

        if result.status == "exact":
            return {
                "status": "exact",
                "model": result.match.model_dump() if result.match else {},
            }

        if result.status == "ambiguous":
            index = arguments.get("index")
            if index is not None:
                candidates = result.candidates
                if not isinstance(index, int) or index < 0 or index >= len(candidates):
                    raise MCPError(
                        f"Index {index} out of range. "
                        f"Valid range: 0-{len(candidates) - 1}."
                    )
                return {
                    "status": "exact",
                    "model": candidates[index].model_dump(),
                }

            return {
                "status": "ambiguous",
                "candidates": [c.model_dump() for c in result.candidates],
                "hint": (
                    "Multiple matches. Re-call resolve_model with index=N "
                    "to pick, or a more specific query."
                ),
            }

        return {
            "status": "not_found",
            "query": query,
        }

    def tool_handlers(self) -> dict:
        return {
            "list_models": self.handle_list_models,
            "resolve_model": self.handle_resolve_model,
        }

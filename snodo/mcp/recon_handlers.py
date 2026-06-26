"""Recon tool handlers for the MCP server.

FILE: snodo/mcp/recon_handlers.py

Mirrors JobToolHandler / ModelToolHandler pattern.
"""

from typing import Any, Dict


class ReconToolHandler:
    """Handles recon, get_recon_status, and get_recon_results tool calls."""

    def __init__(self, project_root: str):
        self.project_root = project_root

    def handle_recon(self, arguments: Dict[str, Any]) -> dict:
        """Dispatch a read-only exploration query to one or more agents.

        Returns a recon_id immediately.
        """
        from snodo.mcp.server import MCPError

        query = arguments.get("query", "")
        if not query:
            raise MCPError("recon requires query")
        paths = arguments.get("paths", ["./"])
        if not isinstance(paths, list) or not paths:
            raise MCPError("recon requires paths (non-empty list)")

        explicit_agents = arguments.get("agents")
        num_agents = arguments.get("num_agents")

        from snodo.recon import ReconManager, ReconError, resolve_recon_agents
        from snodo.config import ConfigManager

        config = ConfigManager().load()
        recon_cfg = config.get("llm", {}).get("recon", {})
        recon_models = recon_cfg.get("models", [])
        recon_default_n = recon_cfg.get("num_agents", 1)

        agents = resolve_recon_agents(
            requested_n=num_agents,
            recon_models=recon_models,
            recon_default_n=recon_default_n,
            explicit_agents=explicit_agents if isinstance(explicit_agents, list) and explicit_agents else None,
        )

        mgr = ReconManager(self.project_root)
        try:
            recon_id = mgr.submit(query, paths, agents)
        except ReconError as e:
            raise MCPError(str(e))

        return {
            "recon_id": recon_id,
            "status": "running",
            "agents": agents,
            "query": query,
        }

    def handle_get_recon_status(self, arguments: Dict[str, Any]) -> dict:
        """Get the status of a recon query."""
        from snodo.mcp.server import MCPError

        recon_id = arguments.get("recon_id", "")
        if not recon_id:
            raise MCPError("get_recon_status requires recon_id")

        from snodo.recon import ReconManager, ReconError

        mgr = ReconManager(self.project_root)
        try:
            return mgr.get_status(recon_id)
        except ReconError as e:
            raise MCPError(str(e))

    def handle_get_recon_results(self, arguments: Dict[str, Any]) -> dict:
        """Get the raw results of a completed recon query."""
        from snodo.mcp.server import MCPError

        recon_id = arguments.get("recon_id", "")
        if not recon_id:
            raise MCPError("get_recon_results requires recon_id")

        from snodo.recon import ReconManager, ReconError

        mgr = ReconManager(self.project_root)
        try:
            return mgr.get_results(recon_id)
        except ReconError as e:
            raise MCPError(str(e))

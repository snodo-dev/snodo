"""CLI recon command — multi-agent codebase exploration.

FILE: snodo/cli/commands/recon_cmd.py
"""

import sys
from types import SimpleNamespace
from typing import List, Optional

import typer


def register(app: typer.Typer) -> None:
    """Register top-level CLI commands onto app (called by discovery loop)."""

    @app.command()
    def recon(
        query: str = typer.Argument(..., help="The exploration question to answer"),
        paths: Optional[List[str]] = typer.Argument(None, help="Paths to search (default: current directory)"),
        num_agents: Optional[int] = typer.Option(None, "--agents", "-n", help="Number of agents to fan out (uses config if omitted)"),
    ):
        """Dispatch a read-only exploration query to one or more agents."""
        args = SimpleNamespace(query=query, paths=paths or ["./"], num_agents=num_agents)
        return recon_command(args)



def recon_command(args) -> int:
    """Dispatch a read-only exploration query to one or more agents."""
    query = getattr(args, "query", "")
    paths = getattr(args, "paths", ["./"])
    num_agents = getattr(args, "num_agents", None)

    if not query:
        print("Error: query is required", file=sys.stderr)
        return 1

    if not isinstance(paths, list) or not paths:
        paths = ["./"]

    from snodo.infrastructure.paths import require_project_root
    from snodo.recon import ReconManager, resolve_recon_agents

    project_root = require_project_root()

    from snodo.config import ConfigManager
    config = ConfigManager().load()
    recon_cfg = config.get("llm", {}).get("recon", {})
    recon_models = recon_cfg.get("models", [])
    recon_default_n = recon_cfg.get("num_agents", 1)

    lanes = resolve_recon_agents(
        requested_n=num_agents,
        recon_models=recon_models,
        recon_default_n=recon_default_n,
    )

    mgr = ReconManager(project_root)
    recon_id = mgr.submit(query, paths, lanes)

    print(f"Recon dispatched: {recon_id}")
    print(f"  Agents: {', '.join(lane[0] for lane in lanes)}")
    for lane in lanes:
        if len(lane) > 1:
            print(f"    Failover order for {lane[0]}: {' → '.join(lane)}")
    print(f"  Query:  {query}")
    print()

    mgr.shutdown(timeout=300.0)

    status_data = mgr.get_status(recon_id)
    results = status_data.get("results", [])

    if not results:
        print(f"Recon {recon_id} completed with no results", file=sys.stderr)
        return 1

    print("Recon complete:")
    for res in results:
        agent_name = res.get("agent", "agent") if isinstance(res, dict) else getattr(res, "agent", "agent")
        model_name = res.get("model", "") if isinstance(res, dict) else getattr(res, "model", "")
        err = res.get("error", "") if isinstance(res, dict) else getattr(res, "error", "")
        content = res.get("result", "") if isinstance(res, dict) else getattr(res, "result", "")
        attempts = res.get("attempts", []) if isinstance(res, dict) else getattr(res, "attempts", [])
        print(f"--- Agent: {agent_name} ({model_name}) ---")
        for attempt in attempts:
            attempt_model = attempt.get("model", "") if isinstance(attempt, dict) else getattr(attempt, "model", "")
            attempt_error = attempt.get("error", None) if isinstance(attempt, dict) else getattr(attempt, "error", None)
            if attempt_error:
                print(f"  tried {attempt_model}: {attempt_error}", file=sys.stderr)
        if err:
            print(f"Error: {err}", file=sys.stderr)
        else:
            print(content)
        print()

    return 0

"""CLI recon command — multi-agent codebase exploration.

FILE: snodo/cli/commands/recon_cmd.py
"""

import sys


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

    agents = resolve_recon_agents(
        requested_n=num_agents,
        recon_models=recon_models,
        recon_default_n=recon_default_n,
    )

    mgr = ReconManager(project_root)
    recon_id = mgr.submit(query, paths, agents)

    print(f"Recon dispatched: {recon_id}")
    print(f"  Agents: {', '.join(agents)}")
    print(f"  Query:  {query}")
    print()
    print(f"Check results: snodo logs {recon_id}")
    return 0

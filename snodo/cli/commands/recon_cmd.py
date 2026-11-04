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
        models: Optional[List[str]] = typer.Option(
            None, "--model", help="Model; models listed override recon configuration, and their count is the number of agents",
        ),
        model_list: Optional[List[str]] = typer.Option(
            None, "--models", help="Comma-separated models; models listed override recon configuration, and their count is the number of agents",
        ),
    ):
        """Dispatch a read-only exploration query to one or more agents."""
        ordered_models = _models_in_argv_order(models, model_list)
        args = SimpleNamespace(
            query=query, paths=paths or ["./"], num_agents=num_agents,
            models=models, model_list=model_list, ordered_models=ordered_models,
        )
        return recon_command(args)



def _models_in_argv_order(models, model_list):
    """Merge CLI option values in their command-line order."""
    argv = sys.argv[1:]
    ordered = []
    index = 0
    while index < len(argv):
        token = argv[index]
        option, separator, inline = token.partition("=")
        if option in ("--model", "--models"):
            value = inline if separator else None
            if value is None and index + 1 < len(argv):
                index += 1
                value = argv[index]
            if value is not None:
                ordered.append((option, value))
        index += 1
    if ordered:
        return ordered
    return [("--model", value) for value in (models or [])] + [
        ("--models", value) for value in (model_list or [])
    ]


def recon_command(args) -> int:
    """Dispatch a read-only exploration query to one or more agents."""
    query = getattr(args, "query", "")
    paths = getattr(args, "paths", ["./"])
    num_agents = getattr(args, "num_agents", None)
    model_values = getattr(args, "models", None) or []
    model_lists = getattr(args, "model_list", None) or []
    entries = getattr(args, "ordered_models", None) or (
        [("--model", value) for value in model_values]
        + [("--models", value) for value in model_lists]
    )
    explicit_models = []
    for option, value in entries:
        if option == "--model":
            explicit_models.append(value)
        else:
            explicit_models.extend(part.strip() for part in value.split(",") if part.strip())

    if explicit_models and num_agents is not None:
        print("Error: --agents/-n cannot be used with --model or --models; agent count comes from the number of models listed.", file=sys.stderr)
        return 1

    if not query:
        print("Error: query is required", file=sys.stderr)
        return 1

    if not isinstance(paths, list) or not paths:
        paths = ["./"]

    from snodo.infrastructure.paths import require_project_root
    from snodo.recon import (
        RECON_DEFAULT_MAX_TOKENS,
        RECON_DEFAULT_MAX_TOOL_TURNS,
        ReconManager,
        resolve_recon_agents_with_notice,
    )

    project_root = require_project_root()

    from snodo.config import ConfigManager
    config = ConfigManager().load()
    recon_cfg = config.get("llm", {}).get("recon", {})
    recon_models = recon_cfg.get("models", [])
    recon_default_n = recon_cfg.get("num_agents", 1)
    max_tool_turns = recon_cfg.get("max_tool_turns", RECON_DEFAULT_MAX_TOOL_TURNS)
    max_tokens = recon_cfg.get("max_tokens", RECON_DEFAULT_MAX_TOKENS)

    if explicit_models:
        from snodo.config import ConfigManager
        from snodo.recon import resolve_agent_model

        config_manager = ConfigManager()
        lanes = []
        for value in explicit_models:
            if "," in value:
                print("Error: --model accepts one model; use --models for a comma-separated list", file=sys.stderr)
                return 1
            model = value.strip()
            if not model:
                print("Error: model names cannot be empty", file=sys.stderr)
                return 1
            model = resolve_agent_model(model)
            provider = ConfigManager.provider_for_model(model)
            providers = config_manager.get_providers()
            if not provider or provider not in providers:
                print(f"Error: unknown model or provider for '{model}'", file=sys.stderr)
                return 1
            if not config_manager.get_key_for_model(model) and not config_manager.provider_uses_oauth(provider):
                print(f"Error: provider for model '{model}' is not configured", file=sys.stderr)
                return 1
            lanes.append([model])
        agent_count_notice = None
    else:
        lanes, agent_count_notice = resolve_recon_agents_with_notice(
            requested_n=num_agents,
            recon_models=recon_models,
            recon_default_n=recon_default_n,
        )

    mgr = ReconManager(project_root)
    recon_id = mgr.submit(
        query, paths, lanes,
        max_tool_turns=max_tool_turns, max_tokens=max_tokens,
    )

    print(f"Recon dispatched: {recon_id}")
    if agent_count_notice:
        print(f"  Agent count: {len(lanes)} ({agent_count_notice})")
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

    from snodo.usage_summary import render_usage_summary, summarize_usage

    usage_records = []
    for result in results:
        get = result.get if isinstance(result, dict) else lambda key, default=None: getattr(result, key, default)
        model = get("model", "unknown") or "unknown"
        usage = get("usage", []) or []
        if usage:
            usage_records.extend({**item, "role": model} for item in usage if isinstance(item, dict))
        else:
            usage_records.append({"role": model})
    state = status_data
    elapsed = None
    if isinstance(state, dict) and state.get("created_at") is not None and state.get("completed_at") is not None:
        elapsed = max(0.0, state["completed_at"] - state["created_at"])
    for line in render_usage_summary(summarize_usage(usage_records), elapsed):
        print(line)
    return 0

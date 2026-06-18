"""snodo models — live provider/model discovery with per-provider cache.

FILE: snodo/cli/commands/models_cmd.py
"""

import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

from snodo.infrastructure.paths import resolve_home

_CACHE_TTL = 24 * 3600
_CACHE_DIR = resolve_home() / "models"


def _cache_path(provider: str) -> Path:
    return _CACHE_DIR / f"{provider}.json"


def _read_cache(provider: str) -> Optional[list]:
    path = _cache_path(provider)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        fetched_at = data.get("fetched_at", 0)
        if time.time() - fetched_at < _CACHE_TTL:
            return data.get("models", [])
    except Exception:
        pass
    return None


def _write_cache(provider: str, models: list) -> None:
    path = _cache_path(provider)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "fetched_at": time.time(),
        "provider": provider,
        "models": models,
    }
    path.write_text(json.dumps(data, indent=2))


def models_command(args) -> int:
    """List configured providers or their models."""
    provider_name = getattr(args, "provider", None)
    flush = getattr(args, "flush", False)

    # Discrete filter flags
    id_contains = getattr(args, "id_contains", None)
    max_output_cost = getattr(args, "max_output_cost", None)
    min_output_cost = getattr(args, "min_output_cost", None)
    max_input_cost = getattr(args, "max_input_cost", None)
    min_context = getattr(args, "min_context", None)

    from snodo.cli.config import ConfigManager
    mgr = ConfigManager()
    providers = mgr.get_providers()

    if not provider_name:
        return _list_providers(providers)

    pc = providers.get(provider_name)
    if not pc:
        print(f"Provider not configured: {provider_name}", file=sys.stderr)
        print(f"  Configured: {', '.join(sorted(providers.keys()))}",
              file=sys.stderr)
        return 1

    models = _get_models(provider_name, pc, force_refresh=flush)
    if not models:
        print(f"No models discovered for {provider_name}")
        return 0

    # Apply filters
    if (id_contains is not None or
        max_output_cost is not None or
        min_output_cost is not None or
        max_input_cost is not None or
        min_context is not None):

        models = _apply_discrete_filters(
            models,
            id_contains=id_contains,
            max_output_cost=max_output_cost,
            min_output_cost=min_output_cost,
            max_input_cost=max_input_cost,
            min_context=min_context,
        )
        if not models:
            print("No models matched the specified filters.")
            return 0

    _print_model_table(provider_name, models)
    print()
    print(f"{len(models)} model(s) from {provider_name}")
    return 0


def _list_providers(providers: dict) -> int:
    configured = []
    for name in sorted(providers.keys()):
        pc = providers[name]
        has_key = bool(pc.api_key or (pc.api_key_env and os.environ.get(pc.api_key_env)))
        if has_key:
            configured.append(name)

    if not configured:
        print("No providers configured. Add a key with: snodo config add <provider> <key>")
        print("  Then run: snodo models --provider=<name>")
        return 0

    print("Configured providers:")
    print()
    for name in configured:
        print(f"  {name:<14} configured")
    print()
    print("Run: snodo models --provider=<name> to list models")
    return 0


def _get_models(provider_name: str, pc, force_refresh: bool = False) -> list:
    if not force_refresh:
        cached = _read_cache(provider_name)
        if cached is not None:
            return cached

    from snodo.infrastructure.model_discovery import _DISCOVERY_DISPATCH
    discover = _DISCOVERY_DISPATCH.get(provider_name)
    if not discover:
        return []

    try:
        results = discover(pc)
    except Exception as e:
        print(f"Discovery failed: {e}", file=sys.stderr)
        return _read_cache(provider_name) or []

    serialized = [m.model_dump() for m in results]
    _write_cache(provider_name, serialized)
    return serialized


def _lookup_price(full_string: str) -> tuple:
    """Return (input_cost_per_1M, output_cost_per_1M) or ("unknown", "unknown")."""
    from snodo.infrastructure.model_catalog import lookup as catalog_lookup
    meta = catalog_lookup(full_string)
    inp = meta.get("input_cost")
    outp = meta.get("output_cost")
    inp_str = f"${float(inp) * 1_000_000:.2f}" if isinstance(inp, (int, float)) else "unknown"
    outp_str = f"${float(outp) * 1_000_000:.2f}" if isinstance(outp, (int, float)) else "unknown"
    return inp_str, outp_str


def _lookup_context(full_string: str) -> str:
    """Return context window as string, or '—' if unknown."""
    from snodo.infrastructure.model_catalog import lookup as catalog_lookup
    meta = catalog_lookup(full_string)
    ctx = meta.get("context", 0)
    return str(ctx) if ctx else "—"
    return "unknown", "unknown"


def _apply_discrete_filters(
    models: list,
    id_contains: Optional[str] = None,
    max_output_cost: Optional[float] = None,
    min_output_cost: Optional[float] = None,
    max_input_cost: Optional[float] = None,
    min_context: Optional[int] = None,
) -> list:
    """Filter models using discrete shell-safe criteria combined with AND."""
    filtered = []
    for m in models:
        # 1. ID / display name check (case-insensitive substring match)
        if id_contains:
            mid = str(m.get("id", "")).lower()
            disp = str(m.get("display_name", "")).lower()
            query = id_contains.lower()
            if query not in mid and query not in disp:
                continue

        # Helper to get cost per 1M tokens
        def get_cost_per_1m(cost_type: str) -> Optional[float]:
            try:
                import litellm
                info = litellm.model_cost.get(m.get("full_string", ""))
                if info:
                    val = info.get(f"{cost_type}_cost_per_token")
                    if val is not None:
                        return float(val) * 1_000_000
            except Exception:
                pass
            return None

        # 2. Max output cost (excludes unknown output cost)
        if max_output_cost is not None:
            out_cost = get_cost_per_1m("output")
            if out_cost is None or out_cost > max_output_cost:
                continue

        # 3. Min output cost (excludes unknown output cost)
        if min_output_cost is not None:
            out_cost = get_cost_per_1m("output")
            if out_cost is None or out_cost < min_output_cost:
                continue

        # 4. Max input cost (excludes unknown input cost)
        if max_input_cost is not None:
            in_cost = get_cost_per_1m("input")
            if in_cost is None or in_cost > max_input_cost:
                continue

        # 5. Min context (excludes context_window == 0)
        if min_context is not None:
            ctx = m.get("context_window", 0)
            try:
                ctx_val = int(ctx) if ctx is not None else 0
            except (ValueError, TypeError):
                ctx_val = 0
            if ctx_val == 0 or ctx_val < min_context:
                continue

        filtered.append(m)
    return filtered


def _print_model_table(provider: str, models: list) -> None:
    """Print a formatted table of models with cost."""
    # Compute column widths
    col_model = max(max(len(m.get("id", "")) for m in models) + len(provider) + 1, 40)
    col_ctx = 10
    col_cost = 22

    header = (
        f" {'MODEL':<{col_model}}  {'CONTEXT':>{col_ctx}}  {'COST in/out (per 1M)':<{col_cost}}"
    )
    print()
    print(f"Provider: {provider}")
    print("-" * len(header))
    print(header)
    print("-" * len(header))

    for m in models:
        mid = m.get("full_string", m.get("id", ""))
        ctx_str = _lookup_context(mid)
        inp, outp = _lookup_price(m.get("full_string", ""))
        cost_str = f"{inp} / {outp}"
        print(f" {mid:<{col_model}}  {ctx_str:>{col_ctx}}  {cost_str:<{col_cost}}")

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
    filter_expr = getattr(args, "filter", "")

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

    if filter_expr:
        models = _apply_filter(models, filter_expr)
        if not models:
            print(f"No models matched filter: {filter_expr}")
            return 0

    _print_model_table(provider_name, models)
    print()
    print(f"{len(models)} model(s) from {provider_name}")
    return 0


def _list_providers(providers: dict) -> int:
    print("Configured providers:")
    print()
    for name in sorted(providers.keys()):
        pc = providers[name]
        has_key = bool(pc.api_key or (pc.api_key_env and os.environ.get(pc.api_key_env)))
        status = "configured" if has_key else "no key"
        print(f"  {name:<14} {status}")
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
    try:
        import litellm
        info = litellm.model_cost.get(full_string)
        if info:
            inp = info.get("input_cost_per_token", 0)
            outp = info.get("output_cost_per_token", 0)
            if inp:
                inp_str = f"${inp * 1_000_000:.2f}"
            else:
                inp_str = "unknown"
            if outp:
                outp_str = f"${outp * 1_000_000:.2f}"
            else:
                outp_str = "unknown"
            return inp_str, outp_str
    except Exception:
        pass
    return "unknown", "unknown"


_FILTER_OPS = {">", "<", ">=", "<=", "=", "=="}


def _apply_filter(models: list, expr: str) -> list:
    """Filter models by a simple expression: field op value."""
    expr = expr.strip()

    # Find the operator
    for op in [">=", "<=", "==", "!=", ">", "<", "="]:
        if op in expr:
            parts = expr.split(op, 1)
            field = parts[0].strip()
            value = parts[1].strip()
            break
    else:
        # No operator found — substring match on id/display_name
        return [m for m in models
                if expr.lower() in (m.get("id", "") + m.get("display_name", "")).lower()]

    numeric_fields = {"context_window"}
    cost_fields = {"input_cost", "output_cost", "input_cost_per_1m", "output_cost_per_1m"}

    def _numeric_val(model: dict, f: str) -> float:
        if f in cost_fields:
            try:
                import litellm
                info = litellm.model_cost.get(model.get("full_string", ""))
                if info:
                    if f in ("input_cost", "input_cost_per_1m"):
                        return info.get("input_cost_per_token", 0) * 1_000_000
                    else:
                        return info.get("output_cost_per_token", 0) * 1_000_000
            except Exception:
                pass
            return 0.0
        return float(model.get(f, 0))

    if field in numeric_fields or field in cost_fields:
        try:
            val = float(value)
        except ValueError:
            return models

        def _pred(model: dict) -> bool:
            n = _numeric_val(model, field)
            if op == ">":
                return n > val
            elif op == "<":
                return n < val
            elif op == ">=":
                return n >= val
            elif op == "<=":
                return n <= val
            elif op in ("=", "=="):
                return abs(n - val) < 1e-10
            elif op == "!=":
                return abs(n - val) >= 1e-10
            return True
        return [m for m in models if _pred(m)]

    # String field — substring match
    def _str_pred(model: dict) -> bool:
        haystack = str(model.get(field, "")).lower()
        return value.lower() in haystack
    return [m for m in models if _str_pred(m)]


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
        ctx = m.get("context_window", 0)
        ctx_str = str(ctx) if ctx else "—"
        inp, outp = _lookup_price(m.get("full_string", ""))
        cost_str = f"{inp} / {outp}"
        print(f" {mid:<{col_model}}  {ctx_str:>{col_ctx}}  {cost_str:<{col_cost}}")

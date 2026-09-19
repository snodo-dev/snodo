"""snodo models — live provider/model discovery with per-provider cache.

FILE: snodo/cli/commands/models_cmd.py
"""

import json
import logging
import os
import statistics
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Optional, Tuple
import typer
from snodo.infrastructure.paths import resolve_home, resolve_project_root

_logger = logging.getLogger(__name__)
_MAX_BENCHMARK_RUNS = 10


def register(app: typer.Typer) -> None:
    """Register top-level CLI commands onto app (called by discovery loop)."""

    @app.command()
    def models(
        provider: Optional[str] = typer.Option(None, "--provider", "-p", help="Provider to list models for"),
        flush: bool = typer.Option(False, "--flush", help="Ignore cache and refetch"),
        stats: bool = typer.Option(False, "--stats", help="Report actual model and coder usage from project records"),
        check: bool = typer.Option(False, "--check", help="Make one cheap live call against each configured model"),
        benchmark: bool = typer.Option(
            False, "--benchmark",
            help="Time a fixed prompt against the selected model. Makes real, billed API calls.",
        ),
        benchmark_runs: int = typer.Option(
            1, "--benchmark-runs", min=1, max=_MAX_BENCHMARK_RUNS,
            help=f"Number of sequential benchmark calls (1-{_MAX_BENCHMARK_RUNS}).",
        ),
        json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
        id: Optional[str] = typer.Option(None, "--id", help="Exact model id"),
        id_contains: Optional[str] = typer.Option(None, "--id-contains", help="Substring on id/display_name (case-insensitive)"),
        max_output_cost: Optional[float] = typer.Option(None, "--max-output-cost", help="Output cost/1M <= value. Excludes unknown costs."),
        min_output_cost: Optional[float] = typer.Option(None, "--min-output-cost", help="Output cost/1M >= value. Excludes unknown costs."),
        max_input_cost: Optional[float] = typer.Option(None, "--max-input-cost", help="Input cost/1M <= value. Excludes unknown costs."),
        min_context: Optional[int] = typer.Option(None, "--min-context", help="Context window >= value. Excludes context==0."),
    ):
        """List configured providers and their models."""
        args = SimpleNamespace(
            provider=provider,
            flush=flush,
            stats=stats,
            check=check,
            benchmark=benchmark,
            benchmark_runs=benchmark_runs,
            json=json,
            id=id,
            id_contains=id_contains,
            max_output_cost=max_output_cost,
            min_output_cost=min_output_cost,
            max_input_cost=max_input_cost,
            min_context=min_context,
        )
        return models_command(args)



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
    except Exception as e:
        _logger.debug("Could not read model cache %s: %s", path, e)
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
    """List configured providers, their models, or project usage stats."""
    if getattr(args, "stats", False):
        return models_stats_command(args)

    if getattr(args, "check", False):
        return models_check_command(args)

    if getattr(args, "benchmark", False):
        return models_benchmark_command(args)

    provider_name = getattr(args, "provider", None)
    flush = getattr(args, "flush", False)

    # Discrete filter flags
    model_id = getattr(args, "id", None)
    id_contains = getattr(args, "id_contains", None)
    max_output_cost = getattr(args, "max_output_cost", None)
    min_output_cost = getattr(args, "min_output_cost", None)
    max_input_cost = getattr(args, "max_input_cost", None)
    min_context = getattr(args, "min_context", None)

    from snodo.config import ConfigManager
    mgr = ConfigManager()
    providers = mgr.get_providers()

    if not provider_name:
        if getattr(args, "json", False):
            from snodo.cli.json_output import emit_json, schema_name
            return emit_json({
                "schema": schema_name("models"),
                "ok": True,
                "provider": None,
                "models": [],
                "providers": _configured_provider_names(providers),
            })
        return _list_providers(providers)

    pc = providers.get(provider_name)
    if not pc:
        if getattr(args, "json", False):
            from snodo.cli.json_output import emit_error
            return emit_error("models", f"Provider not configured: {provider_name}", 1)
        print(f"Provider not configured: {provider_name}", file=sys.stderr)
        print(f"  Configured: {', '.join(sorted(providers.keys()))}",
              file=sys.stderr)
        return 1

    models = _get_models(provider_name, pc, force_refresh=flush)
    if not models:
        if getattr(args, "json", False):
            from snodo.cli.json_output import emit_json, schema_name
            return emit_json({
                "schema": schema_name("models"), "ok": True,
                "provider": provider_name, "models": [],
            })
        print(f"No models discovered for {provider_name}")
        return 0

    # Apply filters
    if (model_id is not None or
        id_contains is not None or
        max_output_cost is not None or
        min_output_cost is not None or
        max_input_cost is not None or
        min_context is not None):

        models = _apply_discrete_filters(
            models,
            model_id=model_id,
            id_contains=id_contains,
            max_output_cost=max_output_cost,
            min_output_cost=min_output_cost,
            max_input_cost=max_input_cost,
            min_context=min_context,
        )
        if not models:
            if getattr(args, "json", False):
                from snodo.cli.json_output import emit_json, schema_name
                return emit_json({
                    "schema": schema_name("models"), "ok": True,
                    "provider": provider_name, "models": [],
                })
            print("No models matched the specified filters.")
            return 0

    if getattr(args, "json", False):
        from snodo.cli.json_output import emit_json, schema_name
        return emit_json({
            "schema": schema_name("models"), "ok": True,
            "provider": provider_name, "models": models,
        })

    _print_model_table(provider_name, models)
    print()
    print(f"{len(models)} model(s) from {provider_name}")
    return 0


def _configured_provider_names(providers: dict) -> list[str]:
    """Return configured providers whose credentials are available."""
    return sorted(
        name for name, pc in providers.items()
        if pc.api_key or (pc.api_key_env and os.environ.get(pc.api_key_env))
    )


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

    from snodo.infrastructure.model_discovery import _DISCOVERY_DISPATCH, _discover_openai_compatible
    discover = _DISCOVERY_DISPATCH.get(provider_name)

    try:
        if discover is not None:
            results = discover(pc)
        else:
            results = _discover_openai_compatible(pc, provider_name=provider_name)
    except Exception as e:
        print(f"Discovery failed: {e}", file=sys.stderr)
        return _read_cache(provider_name) or []

    serialized = [m.model_dump() for m in results]
    _write_cache(provider_name, serialized)
    return serialized


def _lookup_price(full_string: str) -> tuple:
    """Return (input_cost_per_1M, output_cost_per_1M) or ("unknown", "unknown").

    The catalog publishes dollars per million tokens; the litellm fallback
    publishes dollars per token.  ``cost_unit`` from the lookup tells the two
    apart, so a number is never scaled by the wrong factor.  A model that
    resolved but publishes no price prints "none published"; a model that did
    not resolve at all prints "unknown".
    """
    from snodo.infrastructure.model_catalog import lookup as catalog_lookup
    meta = catalog_lookup(full_string)
    inp = meta.get("input_cost")
    outp = meta.get("output_cost")
    unit = meta.get("cost_unit", "per_1m")
    found = meta.get("found", False)

    def _fmt(val) -> str:
        if not isinstance(val, (int, float)):
            return "none published" if found else "unknown"
        if unit == "per_token":
            return f"${float(val) * 1_000_000:.2f}"
        return f"${float(val):.2f}"

    return _fmt(inp), _fmt(outp)


def _format_context(ctx: int) -> str:
    """Render a token count in K/M, keeping the exact value available.

    ``1048576`` -> ``1M``, ``262144`` -> ``256K``, ``8192`` -> ``8K``.
    """
    if ctx >= 1024 * 1024:
        return f"{ctx / (1024 * 1024):.0f}M"
    if ctx >= 1024:
        return f"{ctx / 1024:.0f}K"
    return str(ctx)


def _lookup_context(full_string: str) -> str:
    """Return context window as a K/M string, or '—' if unknown."""
    from snodo.infrastructure.model_catalog import lookup as catalog_lookup
    meta = catalog_lookup(full_string)
    ctx = meta.get("context", 0)
    return _format_context(int(ctx)) if ctx else "—"


def _apply_discrete_filters(
    models: list,
    model_id: Optional[str] = None,
    id_contains: Optional[str] = None,
    max_output_cost: Optional[float] = None,
    min_output_cost: Optional[float] = None,
    max_input_cost: Optional[float] = None,
    min_context: Optional[int] = None,
) -> list:
    """Filter models using discrete shell-safe criteria combined with AND."""
    filtered = []
    for m in models:
        # 1. Exact ID and ID / display name substring checks
        if model_id is not None and m.get("id") != model_id:
            continue
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
                # litellm prints a 'Provider List' banner to stdout whenever it cannot
                # name a provider. Snodo asks it about models it deliberately routes
                # itself, so that is the normal case, not an error worth printing.
                litellm.suppress_debug_info = True
                info = litellm.model_cost.get(m.get("full_string", ""))
                if info:
                    val = info.get(f"{cost_type}_cost_per_token")
                    if val is not None:
                        return float(val) * 1_000_000
            except Exception as e:
                _logger.debug("Could not look up cost for %s: %s", m.get("full_string", ""), e)
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


_SMALL_SAMPLE_THRESHOLD = 10


def _parse_timestamp(val: Any) -> Optional[float]:
    """Parse numeric epoch or ISO timestamp string into epoch float."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        val = val.strip()
        try:
            return float(val)
        except ValueError:
            pass
        try:
            from datetime import datetime
            return datetime.fromisoformat(val).timestamp()
        except Exception:
            return None
    return None


def _estimate_call_cost(model: str, prompt_tokens: Any, completion_tokens: Any) -> Optional[float]:
    """Derive estimated cost from catalog for unpriced recorded calls without making network calls."""
    if prompt_tokens is None and completion_tokens is None:
        return None
    try:
        pt = int(prompt_tokens) if prompt_tokens is not None else 0
        ct = int(completion_tokens) if completion_tokens is not None else 0
    except (ValueError, TypeError):
        return None

    if pt + ct == 0:
        return None

    try:
        from snodo.infrastructure import model_catalog
        orig_fetch = getattr(model_catalog, "_fetch_catalog", None)
        try:
            if orig_fetch is not None:
                model_catalog._fetch_catalog = lambda: None
            meta = model_catalog.lookup(model)
        finally:
            if orig_fetch is not None:
                model_catalog._fetch_catalog = orig_fetch

        if not meta or not meta.get("found"):
            return None

        inp = meta.get("input_cost")
        outp = meta.get("output_cost")
        if not isinstance(inp, (int, float)) or not isinstance(outp, (int, float)):
            return None

        unit = meta.get("cost_unit", "per_1m")
        if unit == "per_1m":
            inp_rate = float(inp) / 1_000_000
            outp_rate = float(outp) / 1_000_000
        else:
            inp_rate = float(inp)
            outp_rate = float(outp)

        return (pt * inp_rate) + (ct * outp_rate)
    except Exception as e:
        _logger.debug("Catalog cost estimation failed for %s: %s", model, e)
        return None


def _model_matches_provider(model: str, provider: str) -> bool:
    """Check if model string matches requested provider."""
    if not provider:
        return True
    model_lower = model.lower()
    prov_lower = provider.lower()
    if model_lower.startswith(f"{prov_lower}/") or model_lower == prov_lower:
        return True
    try:
        from snodo.config import ConfigManager
        detected = ConfigManager._provider_for_model(model)
        if detected and detected.lower() == prov_lower:
            return True
    except Exception as e:
        _logger.debug("Provider detection failed for %s: %s", model, e)
    return False


def _collect_project_stats(
    project_root: Path, provider_filter: Optional[str] = None
) -> Tuple[Dict[str, Any], Dict[str, Any], int]:
    """Aggregate per-model and per-coder statistics from .snodo/jobs/ on disk."""
    jobs_dir = project_root / ".snodo" / "jobs"
    if not jobs_dir.is_dir():
        return {}, {}, 0

    model_stats: Dict[str, Any] = {}
    coder_stats: Dict[str, Any] = {}
    total_jobs = 0

    try:
        job_entries = [
            d for d in sorted(jobs_dir.iterdir())
            if d.is_dir() and (d.name.startswith("j_") or (d / "state.json").exists())
        ]
    except Exception as e:
        _logger.debug("Could not read jobs dir %s: %s", jobs_dir, e)
        return {}, {}, 0

    for job_dir in job_entries:
        state_path = job_dir / "state.json"
        task_path = job_dir / "task.json"

        state: dict = {}
        if state_path.exists():
            try:
                state = json.loads(state_path.read_text())
            except Exception:
                state = {}

        task: dict = {}
        if task_path.exists():
            try:
                task = json.loads(task_path.read_text())
            except Exception:
                task = {}

        total_jobs += 1

        coder = str(task.get("coder") or state.get("coder") or "").strip()
        usage = state.get("usage")
        if not isinstance(usage, list):
            usage = []

        if not coder:
            for r in usage:
                if isinstance(r, dict):
                    if r.get("coder"):
                        coder = str(r["coder"]).strip()
                        break
                    if r.get("role") == "coder" and r.get("model"):
                        coder = str(r["model"]).strip()
                        break
        if not coder:
            coder = str(task.get("model") or "unknown").strip()

        status = str(state.get("status") or "unknown").strip().lower()
        is_completed = (status == "completed")

        started_at = _parse_timestamp(state.get("started_at") or state.get("created_at"))
        completed_at = _parse_timestamp(state.get("completed_at"))

        duration = None
        if started_at is not None and completed_at is not None and completed_at >= started_at:
            duration = completed_at - started_at
        elif state.get("duration_seconds") is not None:
            try:
                duration = float(state["duration_seconds"])
            except (ValueError, TypeError):
                duration = None

        c_entry = coder_stats.setdefault(coder, {
            "total_jobs": 0,
            "completed_jobs": 0,
            "durations": [],
        })
        c_entry["total_jobs"] += 1
        if is_completed:
            c_entry["completed_jobs"] += 1
        if duration is not None and duration >= 0:
            c_entry["durations"].append(duration)

        for r in usage:
            if not isinstance(r, dict):
                continue
            model = str(r.get("model") or "").strip()
            if not model:
                model = str(r.get("coder") or "unknown").strip()

            if provider_filter and not _model_matches_provider(model, provider_filter):
                continue

            m_entry = model_stats.setdefault(model, {
                "calls": 0,
                "output_tokens": 0,
                "rate_tokens": 0,
                "rate_duration_ms": 0.0,
                "durations_ms": [],
                "roles": set(),
                "measured_cost": 0.0,
                "measured_calls": 0,
                "estimated_cost": 0.0,
                "estimated_calls": 0,
                "unpriced_calls": 0,
            })

            m_entry["calls"] += 1

            ct = r.get("completion_tokens")
            pt = r.get("prompt_tokens")
            ct_val = None
            if isinstance(ct, (int, float)):
                ct_val = int(ct)
                m_entry["output_tokens"] += ct_val

            dur = r.get("duration_ms")
            if isinstance(dur, (int, float)) and float(dur) >= 0:
                dur_val = float(dur)
                m_entry["durations_ms"].append(dur_val)
                if ct_val is not None and dur_val > 0:
                    m_entry["rate_tokens"] += ct_val
                    m_entry["rate_duration_ms"] += dur_val

            role = r.get("role")
            if role:
                m_entry["roles"].add(str(role).strip())

            cost = r.get("cost")
            if cost is not None and isinstance(cost, (int, float)) and float(cost) > 0:
                m_entry["measured_cost"] += float(cost)
                m_entry["measured_calls"] += 1
            else:
                est = _estimate_call_cost(model, pt, ct)
                if est is not None and est > 0:
                    m_entry["estimated_cost"] += est
                    m_entry["estimated_calls"] += 1
                else:
                    m_entry["unpriced_calls"] += 1

    return model_stats, coder_stats, total_jobs


def _print_model_stats_table(model_stats: dict) -> None:
    """Print the model usage table."""
    print("Model usage:")
    if not model_stats:
        print("  No model calls recorded.")
        return

    rows = []
    for model, m in model_stats.items():
        calls = m["calls"]
        out_tokens = f"{m['output_tokens']:,}"

        if m["rate_duration_ms"] > 0:
            rate_sec = m["rate_duration_ms"] / 1000.0
            tok_per_sec = m["rate_tokens"] / rate_sec
            rate_str = f"{tok_per_sec:.1f} tok/s"
        else:
            rate_str = "—"

        if m["durations_ms"]:
            mean_ms = sum(m["durations_ms"]) / len(m["durations_ms"])
            dur_str = f"{mean_ms / 1000.0:.2f}s"
        else:
            dur_str = "—"

        mc = m["measured_cost"]
        ec = m["estimated_cost"]
        up = m["unpriced_calls"]

        if mc > 0 and ec == 0 and up == 0:
            cost_str = f"${mc:.4f}"
        elif mc == 0 and ec > 0 and up == 0:
            cost_str = f"${ec:.4f} (est)"
        elif mc > 0 and ec > 0 and up == 0:
            cost_str = f"${mc:.4f} + ${ec:.4f} (est)"
        elif mc > 0 and up > 0:
            est_part = f" + ${ec:.4f} (est)" if ec > 0 else ""
            cost_str = f"${mc:.4f}{est_part} (+ {up} unpriced)"
        elif ec > 0 and up > 0:
            cost_str = f"${ec:.4f} (est, + {up} unpriced)"
        else:
            cost_str = "unpriced"

        roles_str = ", ".join(sorted(m["roles"])) if m["roles"] else "—"

        rows.append((model, str(calls), out_tokens, rate_str, dur_str, cost_str, roles_str, calls))

    rows.sort(key=lambda r: -r[7])

    col_model = max(max(len(r[0]) for r in rows), len("MODEL"))
    col_calls = max(max(len(r[1]) for r in rows), len("CALLS"))
    col_tokens = max(max(len(r[2]) for r in rows), len("OUTPUT TOKENS"))
    col_rate = max(max(len(r[3]) for r in rows), len("RATE"))
    col_dur = max(max(len(r[4]) for r in rows), len("MEAN DURATION"))
    col_cost = max(max(len(r[5]) for r in rows), len("COST"))
    col_roles = max(max(len(r[6]) for r in rows), len("ROLES"))

    header = (
        f" {'MODEL':<{col_model}}  {'CALLS':>{col_calls}}  {'OUTPUT TOKENS':>{col_tokens}}  "
        f"{'RATE':>{col_rate}}  {'MEAN DURATION':>{col_dur}}  {'COST':<{col_cost}}  {'ROLES':<{col_roles}}"
    )
    print("-" * len(header))
    print(header)
    print("-" * len(header))
    for r in rows:
        print(
            f" {r[0]:<{col_model}}  {r[1]:>{col_calls}}  {r[2]:>{col_tokens}}  "
            f"{r[3]:>{col_rate}}  {r[4]:>{col_dur}}  {r[5]:<{col_cost}}  {r[6]:<{col_roles}}"
        )


def _print_coder_stats_table(coder_stats: dict) -> None:
    """Print the coder outcomes table."""
    print("Coder outcomes:")
    if not coder_stats:
        print("  No coder jobs recorded.")
        return

    rows = []
    for coder, c in sorted(coder_stats.items()):
        total = c["total_jobs"]
        completed = c["completed_jobs"]
        pct = (completed / total) if total > 0 else 0.0

        if total < _SMALL_SAMPLE_THRESHOLD:
            completed_str = f"{completed}/{total} ({pct:.0%}) (small sample)"
        else:
            completed_str = f"{completed}/{total} ({pct:.0%})"

        if c["durations"]:
            med_sec = statistics.median(c["durations"])
            if med_sec >= 60:
                typ_dur = f"{med_sec / 60.0:.1f} min"
            else:
                typ_dur = f"{med_sec:.1f}s"
        else:
            typ_dur = "—"

        rows.append((coder, str(total), completed_str, typ_dur))

    col_coder = max(max(len(r[0]) for r in rows), len("CODER"))
    col_jobs = max(max(len(r[1]) for r in rows), len("JOBS"))
    col_comp = max(max(len(r[2]) for r in rows), len("COMPLETED"))
    col_dur = max(max(len(r[3]) for r in rows), len("TYPICAL DURATION"))

    header = (
        f" {'CODER':<{col_coder}}  {'JOBS':>{col_jobs}}  {'COMPLETED':<{col_comp}}  {'TYPICAL DURATION':>{col_dur}}"
    )
    print("-" * len(header))
    print(header)
    print("-" * len(header))
    for r in rows:
        print(
            f" {r[0]:<{col_coder}}  {r[1]:>{col_jobs}}  {r[2]:<{col_comp}}  {r[3]:>{col_dur}}"
        )


def models_stats_command(args) -> int:
    """Report actual model and coder usage from project records."""
    root = resolve_project_root()
    if root is None:
        cwd = Path.cwd()
        if (cwd / ".snodo").is_dir():
            root = str(cwd)

    if root is None:
        if getattr(args, "json", False):
            return _emit_models_stats_json(args, None, None, 0, root)
        print("No jobs recorded for this project.")
        return 0

    jobs_dir = Path(root) / ".snodo" / "jobs"
    if not jobs_dir.is_dir():
        if getattr(args, "json", False):
            return _emit_models_stats_json(args, None, None, 0, root)
        print("No jobs recorded for this project.")
        return 0

    provider_filter = getattr(args, "provider", None)
    model_stats, coder_stats, total_jobs = _collect_project_stats(
        Path(root), provider_filter=provider_filter
    )
    if total_jobs == 0:
        if getattr(args, "json", False):
            return _emit_models_stats_json(args, model_stats, coder_stats, total_jobs, root)
        print("No jobs recorded for this project.")
        return 0

    if getattr(args, "json", False):
        return _emit_models_stats_json(args, model_stats, coder_stats, total_jobs, root)

    _print_model_stats_table(model_stats)
    print()
    _print_coder_stats_table(coder_stats)
    return 0


def _emit_models_stats_json(args, model_stats, coder_stats, total_jobs, root) -> int:
    """Emit the raw, re-aggregatable usage records for ``--stats --json``."""
    if not getattr(args, "json", False):
        return 0
    from snodo.cli.json_output import emit_json, schema_name

    models = {}
    for model, values in (model_stats or {}).items():
        models[model] = {
            **values,
            "roles": sorted(values.get("roles", set())),
        }
    return emit_json({
        "schema": schema_name("models-stats"),
        "ok": True,
        "project_root": str(root) if root is not None else None,
        "provider": getattr(args, "provider", None),
        "total_jobs": total_jobs,
        "models": models,
        "coders": coder_stats or {},
    })


def _configured_models() -> list[tuple[str, str]]:
    """Return the distinct models named by the project's LLM role config."""
    from snodo.config import ConfigManager

    config = ConfigManager().load()
    llm = config.get("llm", {})
    if not isinstance(llm, dict):
        llm = {}
    default_model = config.get("model") or ConfigManager().get_model()

    configured: list[tuple[str, str]] = []
    role_models = [
        ("coder", llm.get("coder", {})),
        ("validator", llm.get("validator") or llm.get("validator_llm") or {}),
        ("classifier", llm.get("classifier", {})),
    ]
    for role, section in role_models:
        model = section.get("model") if isinstance(section, dict) else None
        configured.append((role, model or default_model))

    recon = llm.get("recon", {})
    recon_models = recon.get("models", []) if isinstance(recon, dict) else []
    if recon_models:
        configured.extend(("recon", model) for model in recon_models if model)
    else:
        configured.append(("recon", default_model))

    seen = set()
    return [
        (role, model)
        for role, model in configured
        if model and not (model in seen or seen.add(model))
    ]


def _run_canary_call(model: str, completion_fn: Optional[Any] = None) -> None:
    """Make the smallest request that exercises snodo's constrained LLM path."""
    import litellm
    from snodo.config import ConfigManager

    if completion_fn is None:
        completion_fn = litellm.completion

    kwargs: Dict[str, Any] = {
        "model": ConfigManager.resolve_litellm_model(model),
        "messages": [{"role": "user", "content": "Reply with OK."}],
        "max_tokens": 1,
        "temperature": 0.0,
        "tools": [{
            "type": "function",
            "function": {
                "name": "submit_verdict",
                "description": "Return the result.",
                "parameters": {
                    "type": "object",
                    "properties": {"result": {"type": "string"}},
                    "required": ["result"],
                },
            },
        }],
        "tool_choice": {
            "type": "function",
            "function": {"name": "submit_verdict"},
        },
    }
    api_key = ConfigManager().get_key_for_model(model)
    if api_key:
        kwargs["api_key"] = api_key
    api_base = ConfigManager.resolve_api_base(model)
    if api_base:
        kwargs["api_base"] = api_base
    extra_headers = ConfigManager.resolve_extra_headers(model, task_id="model-check")
    if extra_headers:
        kwargs["extra_headers"] = extra_headers

    completion_fn(**kwargs)


def models_check_command(args) -> int:
    """Check each distinct configured role model without changing engine state."""
    results = []
    for role, model in _configured_models():
        try:
            _run_canary_call(model)
        except Exception as error:
            results.append((role, model, False, str(error)))
        else:
            results.append((role, model, True, ""))

    if not results:
        print("No models configured.")
        return 0

    print("Configured model check:")
    for role, model, healthy, reason in results:
        if healthy:
            print(f"  OK       {model} ({role})")
        else:
            print(f"  FAILED   {model} ({role}): {reason}")
    return 0 if all(healthy for _, _, healthy, _ in results) else 1


# ============================================================================
# --benchmark: time one fixed prompt against one model
# ============================================================================
#
# Why this exists next to --stats: --stats aggregates job telemetry, so its
# tokens-per-second figure moves with prompt size, tool-call count and how much
# the judge read. Two models are never compared there on the same work. A
# benchmark sends ONE prompt — the same one every run — and times the result,
# so two providers produce two numbers worth comparing.
#
# The prompt is part of the measurement, not an argument to it. It lives in
# this directory as ``model_benchmark_prompt.txt`` and is read from disk, never
# constructed from a string in this module: a prompt that varies between runs
# measures nothing across them. Changing that file makes past numbers
# incomparable, which is why the file itself says so.
#
# This makes a real, billed API call. It is reachable only through the explicit
# ``--benchmark`` flag and is never touched by listing, ``--stats`` or any
# other path — the gate and the test suite never issue it.

_BENCHMARK_PROMPT_PATH = Path(__file__).parent / "model_benchmark_prompt.txt"
_BENCHMARK_PROMPT_MARKER = "#--- benchmark prompt below this line ---"


def _load_benchmark_prompt() -> str:
    """Read the fixed benchmark prompt from the repository.

    Deliberately a filesystem read and not a literal: the prompt's identity is
    the file's content, and a benchmark whose prompt is assembled at runtime
    cannot be compared across runs. The file carries a comment header that
    records why changing it invalidates past numbers; the marker line separates
    that header from the prompt text, which is the only part sent to the model.
    Raises ``FileNotFoundError`` if the file is missing, because a silent
    fallback would quietly change the measurement.
    """
    raw = _BENCHMARK_PROMPT_PATH.read_text(encoding="utf-8")
    if _BENCHMARK_PROMPT_MARKER in raw:
        raw = raw.split(_BENCHMARK_PROMPT_MARKER, 1)[1]
    return raw.lstrip("\n")


def _benchmark_prompt_identity(prompt: str) -> str:
    """Return a short, stable identity for a prompt: length and content hash."""
    import hashlib
    digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:12]
    first_line = next(
        (line.strip() for line in prompt.splitlines() if line.strip()), ""
    )
    return f"{first_line!r} ({len(prompt)} chars, sha256:{digest})"


def _select_benchmark_model(provider_name: str, args) -> Optional[str]:
    """Resolve exactly one model from the flags that already select a model.

    Reuses ``--provider`` plus the discrete filters, so the benchmark is scoped
    by the same surface as a listing. More than one match is an error that names
    the candidates rather than an arbitrary pick — benchmarking "a provider" is
    not a measurement.
    """
    from snodo.config import ConfigManager
    providers = ConfigManager().get_providers()
    pc = providers.get(provider_name)
    if not pc:
        print(f"Provider not configured: {provider_name}", file=sys.stderr)
        print(f"  Configured: {', '.join(sorted(providers.keys()))}",
              file=sys.stderr)
        return None

    models = _get_models(provider_name, pc, force_refresh=getattr(args, "flush", False))
    if not models:
        print(f"No models discovered for {provider_name}", file=sys.stderr)
        return None

    models = _apply_discrete_filters(
        models,
        model_id=getattr(args, "id", None),
        id_contains=getattr(args, "id_contains", None),
        max_output_cost=getattr(args, "max_output_cost", None),
        min_output_cost=getattr(args, "min_output_cost", None),
        max_input_cost=getattr(args, "max_input_cost", None),
        min_context=getattr(args, "min_context", None),
    )
    if not models:
        model_id = getattr(args, "id", None)
        if model_id is not None:
            print(f"No model matched --id={model_id}.", file=sys.stderr)
        else:
            print("No models matched the specified filters.", file=sys.stderr)
        return None

    if len(models) > 1:
        print(
            f"--benchmark needs exactly one model, but {len(models)} matched. "
            "Select one with --id=<exact-id> or --id-contains=<substring>:",
            file=sys.stderr,
        )
        for m in models:
            print(f"  {m.get('full_string', m.get('id', ''))}", file=sys.stderr)
        return None

    return models[0].get("full_string") or models[0].get("id")


def _print_benchmark_intent(model: str, prompt: str, runs: int) -> None:
    """Say what is about to be spent, before it is spent."""
    print(f"About to benchmark one model with one fixed prompt ({runs} run(s)).")
    print(f"  model:  {model}")
    print(f"  prompt: {_benchmark_prompt_identity(prompt)}")
    print(f"  source: {_BENCHMARK_PROMPT_PATH}")
    print(f"  This makes {runs} real, billed API call(s).")
    print()


def _run_benchmark_call(
    model: str,
    prompt: str,
    completion_fn: Optional[Any] = None,
    token_counter_fn: Optional[Any] = None,
) -> Dict[str, Any]:
    """Send the prompt once and measure what came back.

    Streaming is used so time to first token is observable. Two rates are
    returned on purpose: a model that streams fast after a slow start is a
    different proposition for a coder than for a validator, and a single figure
    would hide that. ``decode_tok_per_sec`` measures the stream after the first
    token; ``overall_tok_per_sec`` measures everything the caller waited for.

    Token counts prefer the provider's reported usage; when a provider reports
    none, a local tokenizer stands in and the basis says so, because dividing
    one provider's count by another's timing would be a comparison of
    accounting systems rather than of speed.
    """
    from contextlib import nullcontext
    import litellm
    from snodo.config import ConfigManager, provider_env

    if completion_fn is None:
        completion_fn = litellm.completion
    if token_counter_fn is None:
        token_counter_fn = litellm.token_counter

    litellm.drop_params = True
    litellm_model = ConfigManager.resolve_litellm_model(model)

    kwargs: Dict[str, Any] = {
        "model": litellm_model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    api_base = ConfigManager.resolve_api_base(model)
    if api_base:
        kwargs["api_base"] = api_base
    extra_headers = ConfigManager.resolve_extra_headers(model)
    if extra_headers:
        kwargs["extra_headers"] = extra_headers

    start = time.perf_counter()
    ttft: Optional[float] = None
    text_parts: list = []
    usage: Any = None

    for chunk in completion_fn(**kwargs):
        content = None
        try:
            content = getattr(chunk.choices[0].delta, "content", None)
        except (AttributeError, IndexError) as e:
            _logger.debug("Skipping chunk without a content delta: %s", e)
        if content:
            if ttft is None:
                ttft = time.perf_counter() - start
            text_parts.append(content)
        chunk_usage = getattr(chunk, "usage", None)
        if chunk_usage is not None:
            usage = chunk_usage
    wall = time.perf_counter() - start
    text = "".join(text_parts)

    output_tokens: Optional[int] = None
    prompt_tokens: Optional[int] = None
    if usage is not None:
        ct = getattr(usage, "completion_tokens", None)
        if ct is None:
            ct = getattr(usage, "output_tokens", None)
        pt = getattr(usage, "prompt_tokens", None)
        if pt is None:
            pt = getattr(usage, "input_tokens", None)
        if isinstance(ct, int):
            output_tokens = ct
        if isinstance(pt, int):
            prompt_tokens = pt

    if output_tokens is not None:
        counts_basis = "provider-reported usage"
    else:
        output_tokens = int(token_counter_fn(model=litellm_model, text=text))
        counts_basis = "local tokenizer estimate (provider reported no usage)"
    if prompt_tokens is None:
        prompt_tokens = int(token_counter_fn(model=litellm_model, text=prompt))

    overall_rate = output_tokens / wall if wall > 0 and output_tokens else None
    decode_rate = None
    if ttft is not None and wall > ttft and output_tokens > 1:
        decode_rate = (output_tokens - 1) / (wall - ttft)

    return {
        "output_tokens": output_tokens,
        "prompt_tokens": prompt_tokens,
        "counts_basis": counts_basis,
        "time_to_first_token": ttft,
        "wall_seconds": wall,
        "decode_tok_per_sec": decode_rate,
        "overall_tok_per_sec": overall_rate,
    }


def _print_benchmark_report(model: str, prompt: str, result: Dict[str, Any]) -> None:
    """Print the measurement, naming the prompt, the counts and the timing basis."""
    def _rate(val: Optional[float]) -> str:
        return f"{val:.1f}" if val is not None else "n/a"

    ttft = result["time_to_first_token"]
    ttft_str = f"{ttft:.2f}s" if ttft is not None else "n/a"

    print(f"Benchmark result: {model}")
    print(f"  prompt       {_benchmark_prompt_identity(prompt)}")
    print(f"  prompt file  {_BENCHMARK_PROMPT_PATH}")
    print(
        f"  tokens       prompt {result['prompt_tokens']} / "
        f"output {result['output_tokens']}  ({result['counts_basis']})"
    )
    print(
        f"  timing       first token {ttft_str}, "
        f"total wall {result['wall_seconds']:.2f}s"
    )
    print(
        f"  throughput   decode {_rate(result['decode_tok_per_sec'])} output tok/s "
        f"(after first token); overall {_rate(result['overall_tok_per_sec'])} "
        "output tok/s (including first-token wait)"
    )


def _print_benchmark_distribution(
    model: str,
    prompt: str,
    results: list,
    attempted_runs: int,
) -> None:
    """Print statistics over successful runs without averaging output lengths."""
    def _stats(key: str) -> Optional[Tuple[float, float]]:
        values = [r[key] for r in results if r.get(key) is not None]
        if not values:
            return None
        return statistics.median(values), statistics.mean(values)

    def _format_stats(stats: Optional[Tuple[float, float]], suffix: str) -> str:
        if stats is None:
            return "n/a"
        median, mean = stats
        return f"median {median:.2f}{suffix}, mean {mean:.2f}{suffix}"

    print(f"Benchmark result: {model}")
    print(f"  runs         {len(results)} succeeded / {attempted_runs} attempted")
    print(f"  prompt       {_benchmark_prompt_identity(prompt)}")
    print(f"  prompt file  {_BENCHMARK_PROMPT_PATH}")
    print("  statistics   successful runs only; output lengths are not averaged")
    print(f"  timing       first token {_format_stats(_stats('time_to_first_token'), 's')}")
    print(
        f"  throughput   decode "
        f"{_format_stats(_stats('decode_tok_per_sec'), ' output tok/s')}"
    )


def _benchmark_json_payload(
    model: str,
    prompt: str,
    samples: list,
    attempted_runs: int,
) -> dict:
    """Build the stable machine payload without changing benchmark arithmetic."""
    successful = [sample for sample in samples if sample.get("ok")]

    def _distribution(key: str) -> Optional[dict]:
        values = [sample[key] for sample in successful if sample.get(key) is not None]
        if not values:
            return None
        return {"median": statistics.median(values), "mean": statistics.mean(values)}

    import hashlib
    from snodo.version import __version__
    return {
        "schema": "snodo.models-benchmark.v1",
        "ok": bool(successful),
        "model": model,
        "snodo_version": __version__,
        "prompt": {
            "file": str(_BENCHMARK_PROMPT_PATH),
            "chars": len(prompt),
            "sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        },
        "attempted_runs": attempted_runs,
        "succeeded_runs": len(successful),
        "samples": samples,
        "statistics": {
            "time_to_first_token": _distribution("time_to_first_token"),
            "decode_tok_per_sec": _distribution("decode_tok_per_sec"),
            "overall_tok_per_sec": _distribution("overall_tok_per_sec"),
        },
    }


def models_benchmark_command(args) -> int:
    """Benchmark one fixed prompt against one model selected by the model flags."""
    provider_name = getattr(args, "provider", None)
    if not provider_name:
        if getattr(args, "json", False):
            from snodo.cli.json_output import emit_error
            return emit_error("models-benchmark", "--benchmark requires --provider=<name>", 1)
        print(
            "--benchmark requires --provider=<name> (and, when a provider has "
            "more than one model, --id=<exact-id> or --id-contains=<substring>).",
            file=sys.stderr,
        )
        return 1

    model = _select_benchmark_model(provider_name, args)
    if model is None:
        if getattr(args, "json", False):
            from snodo.cli.json_output import emit_error
            return emit_error("models-benchmark", "Could not select exactly one benchmark model", 1)
        return 1

    prompt = _load_benchmark_prompt()
    runs = getattr(args, "benchmark_runs", 1)
    if runs < 1 or runs > _MAX_BENCHMARK_RUNS:
        if getattr(args, "json", False):
            from snodo.cli.json_output import emit_error
            return emit_error(
                "models-benchmark",
                f"--benchmark-runs must be between 1 and {_MAX_BENCHMARK_RUNS}.",
                1,
            )
        print(
            f"--benchmark-runs must be between 1 and {_MAX_BENCHMARK_RUNS}.",
            file=sys.stderr,
        )
        return 1

    json_mode = getattr(args, "json", False)
    if not json_mode:
        _print_benchmark_intent(model, prompt, runs)
    results = []
    samples = []
    for run_number in range(1, runs + 1):
        try:
            result = _run_benchmark_call(model, prompt)
            results.append(result)
            samples.append({"run": run_number, "ok": True, **result})
        except Exception as e:
            samples.append({"run": run_number, "ok": False, "error": str(e)})
            if runs == 1:
                print(f"Benchmark call failed: {e}", file=sys.stderr)
            else:
                print(f"Benchmark run {run_number}/{runs} failed: {e}", file=sys.stderr)

    if not results:
        if json_mode:
            from snodo.cli.json_output import emit_json
            emit_json(_benchmark_json_payload(model, prompt, samples, runs), exit_code=1)
        print(f"All {runs} benchmark run(s) failed.", file=sys.stderr)
        return 1

    if json_mode:
        from snodo.cli.json_output import emit_json
        return emit_json(_benchmark_json_payload(model, prompt, samples, runs))

    if runs == 1:
        _print_benchmark_report(model, prompt, results[0])
    else:
        _print_benchmark_distribution(model, prompt, results, runs)
    return 0

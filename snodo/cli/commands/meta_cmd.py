"""snodo meta — compact task/job summary with timing, tokens, cost, highlight.

FILE: snodo/cli/commands/meta_cmd.py
"""

import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

import typer

from snodo.infrastructure.paths import resolve_project_root


def register(app: typer.Typer) -> None:
    """Register top-level CLI commands onto app (called by discovery loop)."""

    @app.command()
    def meta(
        composite_id: str = typer.Argument(..., help="Job ID (j_xxx) or Task ID (task_xxx)"),
        json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
    ):
        """Show a compact summary for a job or task."""
        args = SimpleNamespace(composite_id=composite_id, json=json)
        return meta_command(args)


def _project_root_or_error(json_out: bool = False) -> Optional[str]:
    root = resolve_project_root()
    if root is None:
        if json_out:
            from snodo.cli.json_output import emit_error
            emit_error("meta", "Not inside a snodo project.", 1)
            raise SystemExit(1)
        print("Not inside a snodo project.", file=sys.stderr)
        raise SystemExit(1)
    return root


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _duration(start: float, end: float) -> str:
    d = (end - start) if start and end else 0
    return f"{d:.1f}s" if d else "—"


def _fmt_tokens(t: int) -> str:
    if t >= 1000:
        return f"{t / 1000:.1f}k"
    return str(t)


def _fmt_cost(cost: Any) -> str:
    if cost is None:
        return "unknown"
    try:
        return f"${float(cost):.4f}"
    except (ValueError, TypeError):
        return "unknown"


def _summarize_cost(records: list) -> Tuple[str, float, bool]:
    total = 0.0
    partial = False
    for r in records:
        c = r.get("cost")
        if c is None:
            partial = True
        else:
            try:
                total += float(c)
            except (ValueError, TypeError):
                partial = True
    cost_str = _fmt_cost(total)
    if partial:
        cost_str = "partial (" + cost_str + ")"
    return cost_str, total, partial


def _summarize_tokens(records: list) -> Tuple[int, int, int]:
    prompt = sum(r.get("prompt_tokens", 0) for r in records)
    completion = sum(r.get("completion_tokens", 0) for r in records)
    return prompt, completion, prompt + completion


def _tool_telemetry_metrics(records: list) -> Dict[str, Any]:
    """Compute structured dictionary of per-turn tool-loop telemetry metrics."""
    if not records:
        return {}

    metrics: Dict[str, Any] = {}

    coder_turns = [r for r in records if r.get("role") == "coder"]
    if coder_turns:
        total = len(coder_turns)
        first_submit = next(
            (r.get("turn_index") for r in coder_turns if r.get("tool") == "submit_files"),
            None,
        )
        if first_submit is not None:
            orient = first_submit - 1
            metrics["orientation"] = {
                "turns_before_first_submit": orient,
                "total_coder_turns": total,
                "orientation_ratio": orient / total if total else 0.0,
            }
        else:
            metrics["orientation"] = {
                "turns_before_first_submit": None,
                "total_coder_turns": total,
                "orientation_ratio": None,
            }

    reads = [r for r in records if r.get("tool") not in ("submit_files", "submit_verdict")]
    if reads:
        hits = sum(1 for r in reads if r.get("read_hit"))
        misses = len(reads) - hits
        metrics["path_miss_rate"] = {
            "misses": misses,
            "total_reads": len(reads),
            "miss_rate": misses / len(reads) if reads else 0.0,
        }

    by_depth: Dict[int, Dict[str, int]] = {}
    for r in reads:
        d = r.get("depth", 0)
        by_depth.setdefault(d, {"total": 0, "hits": 0})
        by_depth[d]["total"] += 1
        if r.get("read_hit"):
            by_depth[d]["hits"] += 1
    if by_depth:
        depth_map = {}
        for d in sorted(by_depth):
            t = by_depth[d]["total"]
            h = by_depth[d]["hits"]
            depth_map[str(d)] = {
                "hits": h,
                "total": t,
                "re_read_rate": (h / t) if t else 0.0,
            }
        metrics["re_read_by_depth"] = depth_map

    submits = [r for r in records if r.get("tool") == "submit_files" and r.get("submit_bytes")]
    if submits:
        sizes = sorted(r["submit_bytes"] for r in submits)
        n = len(sizes)
        median = sizes[n // 2] if n % 2 else (sizes[n // 2 - 1] + sizes[n // 2]) / 2
        metrics["submit_size"] = {
            "count": n,
            "median_bytes": int(median),
            "max_bytes": sizes[-1],
        }

    return metrics


def _tool_telemetry_summary(records: list) -> List[str]:
    """Summarize per-turn tool-loop telemetry for human display."""
    if not records:
        return []

    lines = []
    coder_turns = [r for r in records if r.get("role") == "coder"]
    if coder_turns:
        total = len(coder_turns)
        first_submit = next(
            (r.get("turn_index") for r in coder_turns if r.get("tool") == "submit_files"),
            None,
        )
        if first_submit is not None:
            orient = first_submit - 1
            lines.append(
                f"  Orientation: {orient}/{total} turns before first submit "
                f"({orient / total:.0%} of coder turns)"
            )
        else:
            lines.append(f"  Orientation: no submit_files recorded ({total} coder turns)")

    reads = [r for r in records if r.get("tool") not in ("submit_files", "submit_verdict")]
    if reads:
        hits = sum(1 for r in reads if r.get("read_hit"))
        lines.append(
            f"  Path miss rate: {len(reads) - hits}/{len(reads)} reads were misses "
            f"({(len(reads) - hits) / len(reads):.0%})"
        )

    by_depth: Dict[int, Dict[str, int]] = {}
    for r in reads:
        d = r.get("depth", 0)
        by_depth.setdefault(d, {"total": 0, "hits": 0})
        by_depth[d]["total"] += 1
        if r.get("read_hit"):
            by_depth[d]["hits"] += 1
    if by_depth:
        parts = []
        for d in sorted(by_depth):
            t = by_depth[d]["total"]
            h = by_depth[d]["hits"]
            parts.append(f"depth {d}: {h}/{t} re-reads")
        lines.append("  Re-read by depth: " + " | ".join(parts))

    submits = [r for r in records if r.get("tool") == "submit_files" and r.get("submit_bytes")]
    if submits:
        sizes = sorted(r["submit_bytes"] for r in submits)
        n = len(sizes)
        median = sizes[n // 2] if n % 2 else (sizes[n // 2 - 1] + sizes[n // 2]) / 2
        lines.append(
            f"  Submit size: {n} submit(s), median {int(median)} bytes, "
            f"max {sizes[-1]} bytes"
        )

    return lines


def _per_role_tokens(records: list) -> List[Tuple[str, int, int]]:
    """Return [(role, prompt_tok, completion_tok), ...] sorted by total desc."""
    roles: dict = {}
    for r in records:
        role = r.get("role", "unknown")
        if role not in roles:
            roles[role] = {"prompt": 0, "completion": 0}
        roles[role]["prompt"] += r.get("prompt_tokens", 0)
        roles[role]["completion"] += r.get("completion_tokens", 0)
    items = [(role, v["prompt"], v["completion"]) for role, v in roles.items()]
    items.sort(key=lambda x: -(x[1] + x[2]))
    return items


def _highlight(halt: dict, tokens: int, cost_str: str) -> str:
    if not halt:
        return "completed — no halt data"
    fd = halt.get("final_decision", "unknown")
    if fd == "completed":
        artifacts = halt.get("artifacts_count", 0)
        return f"completed — {artifacts} artifacts, {_fmt_tokens(tokens)} tok, {cost_str}"
    if fd == "blocker":
        phase = halt.get("phase", "unknown")
        pre = halt.get("pre_validation") or {}
        results = pre.get("validator_results", [])
        blocker = next((r for r in results if r.get("severity") == "blocker"), None)
        if blocker:
            reason = (blocker.get("justification", "") or "")[:60]
            return f"blocked at {phase}: {blocker['validator_id']} — {reason}"
        reason = halt.get("blocker_reason", "") or ""
        return f"blocked at {phase}: {reason}" if reason else f"blocked at {phase}"
    if fd == "escalate":
        phase = halt.get("phase", "unknown")
        return f"escalated at {phase}: needs human review"
    return f"failed: {fd}"


def meta_command(args) -> int:
    """Show a compact summary for a job or task."""
    composite_id = getattr(args, "composite_id", "")
    json_out = bool(getattr(args, "json", False))
    if not composite_id:
        if json_out:
            from snodo.cli.json_output import emit_error
            return emit_error("meta", "Usage: snodo meta <job_id (j_xxx)> or <task_id (task_xxx)>", 1)
        print("Usage: snodo meta <job_id (j_xxx)> or <task_id (task_xxx)>", file=sys.stderr)
        return 1

    project_root = _project_root_or_error(json_out=json_out)
    if project_root is None:
        return 1

    if composite_id.startswith("j_"):
        return _meta_job(project_root, composite_id, json_out=json_out)
    if composite_id.startswith("task_"):
        return _meta_task(project_root, composite_id, json_out=json_out)

    # Try job first, then task
    if Path(project_root, ".snodo", "jobs", composite_id).is_dir():
        return _meta_job(project_root, composite_id, json_out=json_out)
    if Path(project_root, ".snodo", "tasks", composite_id).is_dir():
        return _meta_task(project_root, composite_id, json_out=json_out)
    return _meta_task(project_root, composite_id, force=True, json_out=json_out)


def _meta_job(project_root: str, job_id: str, json_out: bool = False) -> int:
    jobs_dir = Path(project_root) / ".snodo" / "jobs"
    job_dir = jobs_dir / job_id
    if not job_dir.is_dir():
        if json_out:
            from snodo.cli.json_output import emit_error
            return emit_error("meta", f"Job not found: {job_id}", 1)
        print(f"Job not found: {job_id}", file=sys.stderr)
        return 1

    state = _read_json(job_dir / "state.json")
    task = _read_json(job_dir / "task.json")
    usage = state.get("usage", [])
    telemetry = state.get("tool_telemetry", [])
    halt = state.get("halt", {})

    if not isinstance(usage, list):
        usage = []
    if not isinstance(telemetry, list):
        telemetry = []

    status = state.get("status", "unknown")
    created = state.get("created_at", 0)
    started = state.get("started_at", 0)
    completed = state.get("completed_at", 0)
    dur_val = (completed - (started or created)) if (started or created) and completed else 0.0
    dur = _duration(started or created, completed or time.time())

    prompt_tok, comp_tok, total_tok = _summarize_tokens(usage)
    cost_str, total_cost, _ = _summarize_cost(usage)
    role_rows = _per_role_tokens(usage)
    hl = _highlight(halt, total_tok, cost_str)

    desc = task.get("description", "")[:80]
    model = task.get("model", "")

    if json_out:
        from snodo.cli.json_output import emit_json, schema_name
        payload = {
            "schema": schema_name("meta"),
            "ok": True,
            "id": job_id,
            "type": "job",
            "status": status,
            "duration_seconds": round(dur_val, 2),
            "description": desc,
            "model": model,
            "tokens": {
                "prompt": prompt_tok,
                "completion": comp_tok,
                "total": total_tok,
            },
            "cost": total_cost,
            "cost_formatted": cost_str,
            "roles": [
                {
                    "role": role,
                    "prompt_tokens": p,
                    "completion_tokens": c,
                    "total_tokens": p + c,
                }
                for role, p, c in role_rows
            ],
            "tool_telemetry": telemetry,
            "tool_telemetry_summary": _tool_telemetry_metrics(telemetry),
            "highlight": hl,
        }
        return emit_json(payload)

    print(f"Job {job_id}  [{status}]  {dur}")
    if desc:
        print(f"  Task: {desc}")
    if model:
        print(f"  Model: {model}")
    print(f"  Tokens: {_fmt_tokens(total_tok)} (prompt {_fmt_tokens(prompt_tok)} / completion {_fmt_tokens(comp_tok)})")
    print(f"  Cost: {cost_str}")
    if role_rows:
        parts = [f"{role} {_fmt_tokens(p + c)}" for role, p, c in role_rows]
        print(f"  By role: {' | '.join(parts)}")
    telemetry_lines = _tool_telemetry_summary(telemetry)
    if telemetry_lines:
        print("  Tool-loop telemetry:")
        for line in telemetry_lines:
            print(line)
    print(f"  Highlight: {hl}")
    return 0


def _meta_task(project_root: str, task_id: str, force: bool = False, json_out: bool = False) -> int:
    task_dir = Path(project_root) / ".snodo" / "tasks" / task_id
    jobs_dir = Path(project_root) / ".snodo" / "jobs"

    task_state = _read_json(task_dir / "state.json") if task_dir.is_dir() else {}

    matching = []
    if jobs_dir.is_dir():
        for entry in sorted(jobs_dir.iterdir()):
            if not entry.is_dir() or not entry.name.startswith("j_"):
                continue
            task_json = entry / "task.json"
            if not task_json.exists():
                continue
            try:
                td = json.loads(task_json.read_text())
            except Exception:
                continue
            if td.get("task_id") == task_id or td.get("description", "").startswith(task_id):
                matching.append(entry.name)

    if not task_state and not matching:
        msg = f"No jobs or task state found for task {task_id}." if not force else f"No record found for {task_id}."
        if json_out:
            from snodo.cli.json_output import emit_error
            return emit_error("meta", msg, 1)
        print(f"No jobs found for task {task_id}." if not force else f"No jobs found for {task_id}.", file=sys.stderr)
        return 1

    # Aggregate usage and telemetry from task_state and matching jobs
    all_usage: List[dict] = []
    if isinstance(task_state.get("usage"), list):
        all_usage.extend(task_state["usage"])

    all_telemetry: List[dict] = []
    if isinstance(task_state.get("tool_telemetry"), list):
        all_telemetry.extend(task_state["tool_telemetry"])

    job_lines = []
    earliest_start = float("inf")
    latest_end = 0.0

    if task_state.get("started_at") or task_state.get("created_at"):
        s = task_state.get("started_at") or task_state.get("created_at", 0)
        if s and s < earliest_start:
            earliest_start = s
    if task_state.get("completed_at"):
        e = task_state.get("completed_at", 0)
        if e and e > latest_end:
            latest_end = e

    final_halt = task_state.get("halt") if isinstance(task_state.get("halt"), dict) else None

    for jid in matching:
        state = _read_json(jobs_dir / jid / "state.json")
        usage = state.get("usage", [])
        if isinstance(usage, list):
            all_usage.extend(usage)

        telemetry = state.get("tool_telemetry", [])
        if isinstance(telemetry, list):
            all_telemetry.extend(telemetry)

        s = state.get("started_at") or state.get("created_at", 0)
        e = state.get("completed_at", 0)
        if s and s < earliest_start:
            earliest_start = s
        if e and e > latest_end:
            latest_end = e

        halt = state.get("halt", {})
        if isinstance(halt, dict) and halt:
            final_halt = halt

        j_prompt, j_comp, j_tot = _summarize_tokens(usage if isinstance(usage, list) else [])
        j_cost, _, _ = _summarize_cost(usage if isinstance(usage, list) else [])
        hl = _highlight(halt, j_tot, j_cost)
        job_lines.append(f"    {jid}  {hl}")

    prompt_tok, comp_tok, total_tok = _summarize_tokens(all_usage)
    cost_str, total_cost, _ = _summarize_cost(all_usage)
    role_rows = _per_role_tokens(all_usage)
    dur_val = (latest_end - earliest_start) if earliest_start < float("inf") and latest_end else 0.0
    dur = _duration(earliest_start if earliest_start < float("inf") else 0, latest_end or time.time())

    status = task_state.get("status")
    if not status:
        if final_halt:
            status = final_halt.get("final_decision", "unknown")
        else:
            status = "completed" if matching else "unknown"

    hl = _highlight(final_halt or {}, total_tok, cost_str)
    desc = task_state.get("description", "")

    if json_out:
        from snodo.cli.json_output import emit_json, schema_name
        payload = {
            "schema": schema_name("meta"),
            "ok": True,
            "id": task_id,
            "type": "task",
            "status": status,
            "duration_seconds": round(dur_val, 2),
            "description": desc,
            "tokens": {
                "prompt": prompt_tok,
                "completion": comp_tok,
                "total": total_tok,
            },
            "cost": total_cost,
            "cost_formatted": cost_str,
            "roles": [
                {
                    "role": role,
                    "prompt_tokens": p,
                    "completion_tokens": c,
                    "total_tokens": p + c,
                }
                for role, p, c in role_rows
            ],
            "tool_telemetry": all_telemetry,
            "tool_telemetry_summary": _tool_telemetry_metrics(all_telemetry),
            "highlight": hl,
            "jobs": matching,
        }
        return emit_json(payload)

    # Human-readable output
    if matching:
        print(f"Task {task_id}  {len(matching)} job(s)  [{status}]  total {dur}")
    else:
        print(f"Task {task_id}  [{status}]  {dur}")
    if desc:
        print(f"  Task: {desc}")
    print(f"  Tokens: {_fmt_tokens(total_tok)} (prompt {_fmt_tokens(prompt_tok)} / completion {_fmt_tokens(comp_tok)})")
    print(f"  Cost: {cost_str}")
    if role_rows:
        parts = [f"{role} {_fmt_tokens(p + c)}" for role, p, c in role_rows]
        print(f"  By role: {' | '.join(parts)}")
    telemetry_lines = _tool_telemetry_summary(all_telemetry)
    if telemetry_lines:
        print("  Tool-loop telemetry:")
        for line in telemetry_lines:
            print(line)
    print(f"  Highlight: {hl}")
    if job_lines:
        print()
        print("  Jobs:")
        for line in job_lines:
            print(line)
    return 0

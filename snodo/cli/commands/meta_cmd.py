"""snodo meta — compact task/job summary with timing, tokens, cost, highlight.

FILE: snodo/cli/commands/meta_cmd.py
"""

import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import typer

from snodo.infrastructure.paths import resolve_project_root


def register(app: typer.Typer) -> None:
    """Register top-level CLI commands onto app (called by discovery loop)."""

    @app.command()
    def meta(
        composite_id: str = typer.Argument(..., help="Job ID (j_xxx) or Task ID (task_xxx)"),
    ):
        """Show a compact summary for a job or task."""
        args = SimpleNamespace(composite_id=composite_id)
        return meta_command(args)



def _project_root_or_error() -> str:
    root = resolve_project_root()
    if root is None:
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


def _fmt_cost(cost) -> str:
    if cost is None:
        return "unknown"
    try:
        return f"${float(cost):.4f}"
    except (ValueError, TypeError):
        return "unknown"


def _summarize_cost(records: list) -> tuple:
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


def _summarize_tokens(records: list) -> tuple:
    prompt = sum(r.get("prompt_tokens", 0) for r in records)
    completion = sum(r.get("completion_tokens", 0) for r in records)
    return prompt, completion, prompt + completion


def _per_role_tokens(records: list) -> list:
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
    if fd == "blocked":
        phase = halt.get("phase", "unknown")
        pre = halt.get("pre_validation") or {}
        results = pre.get("validator_results", [])
        blocker = next((r for r in results if r.get("severity") == "blocker"), None)
        if blocker:
            reason = (blocker.get("justification", "") or "")[:60]
            return f"blocked at {phase}: {blocker['validator_id']} — {reason}"
        reason = halt.get("blocker_reason", "") or ""
        return f"blocked at {phase}: {reason}" if reason else f"blocked at {phase}"
    return f"failed: {fd}"


def meta_command(args) -> int:
    """Show a compact summary for a job or task."""
    composite_id = getattr(args, "composite_id", "")
    if not composite_id:
        print("Usage: snodo meta <job_id (j_xxx)> or <task_id (task_xxx)>", file=sys.stderr)
        return 1

    project_root = _project_root_or_error()

    if composite_id.startswith("j_"):
        return _meta_job(project_root, composite_id)
    if composite_id.startswith("task_"):
        return _meta_task(project_root, composite_id)

    # Try job first, then task
    if Path(project_root, ".snodo", "jobs", composite_id).is_dir():
        return _meta_job(project_root, composite_id)
    return _meta_task(project_root, composite_id, force=True)


def _meta_job(project_root: str, job_id: str) -> int:
    jobs_dir = Path(project_root) / ".snodo" / "jobs"
    job_dir = jobs_dir / job_id
    if not job_dir.is_dir():
        print(f"Job not found: {job_id}", file=sys.stderr)
        return 1

    state = _read_json(job_dir / "state.json")
    task = _read_json(job_dir / "task.json")
    usage = state.get("usage", [])
    halt = state.get("halt", {})

    if not isinstance(usage, list):
        usage = []

    status = state.get("status", "unknown")
    created = state.get("created_at", 0)
    started = state.get("started_at", 0)
    completed = state.get("completed_at", 0)
    dur = _duration(started or created, completed or time.time())

    prompt_tok, comp_tok, total_tok = _summarize_tokens(usage)
    cost_str, _, _ = _summarize_cost(usage)
    role_rows = _per_role_tokens(usage)
    hl = _highlight(halt, total_tok, cost_str)

    desc = task.get("description", "")[:80]
    model = task.get("model", "")

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
    print(f"  Highlight: {hl}")
    return 0


def _meta_task(project_root: str, task_id: str, force: bool = False) -> int:
    jobs_dir = Path(project_root) / ".snodo" / "jobs"
    if not jobs_dir.is_dir():
        print("No jobs directory found.", file=sys.stderr)
        return 1

    matching = []
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

    if not matching and not force:
        print(f"No jobs found for task {task_id}.", file=sys.stderr)
        return 1
    if not matching and force:
        print(f"No jobs found for {task_id}.", file=sys.stderr)
        return 1

    total_prompt = 0
    total_comp = 0
    total_cost = 0.0
    has_partial = False
    job_lines = []
    final_halt = None
    earliest_start = float("inf")
    latest_end = 0

    for jid in matching:
        state = _read_json(jobs_dir / jid / "state.json")
        usage = state.get("usage", [])
        if not isinstance(usage, list):
            usage = []

        p, c, t = _summarize_tokens(usage)
        total_prompt += p
        total_comp += c

        cost_str, _, partial = _summarize_cost(usage)
        total_cost += sum(float(r.get("cost") or 0) for r in usage if r.get("cost") is not None)
        if partial:
            has_partial = True

        s = state.get("started_at") or state.get("created_at", 0)
        e = state.get("completed_at", 0)
        if s and s < earliest_start:
            earliest_start = s
        if e and e > latest_end:
            latest_end = e

        halt = state.get("halt", {})
        if isinstance(halt, dict) and halt:
            final_halt = halt

        hl = _highlight(halt, t, cost_str)
        job_lines.append(f"    {jid}  {hl}")

    cost_str = _fmt_cost(total_cost)
    if has_partial:
        cost_str = "partial (" + cost_str + ")"
    total_tok = total_prompt + total_comp
    dur = _duration(earliest_start if earliest_start < float("inf") else 0, latest_end or time.time())
    fd = "unknown"
    if final_halt:
        fd = final_halt.get("final_decision", "unknown")
    hl = _highlight(final_halt or {}, total_tok, cost_str)

    print(f"Task {task_id}  {len(matching)} job(s)  [{fd}]  total {dur}")
    print(f"  Tokens: {_fmt_tokens(total_tok)} (prompt {_fmt_tokens(total_prompt)} / completion {_fmt_tokens(total_comp)})")
    print(f"  Cost: {cost_str}")
    print(f"  Highlight: {hl}")
    if job_lines:
        print()
        print("  Jobs:")
        for line in job_lines:
            print(line)
    return 0

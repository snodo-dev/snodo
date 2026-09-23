"""Local task-solution baselines for ``snodo models --set-baseline``."""

import hashlib
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from snodo.cli.json_output import emit_error, emit_json, schema_name
from snodo.infrastructure.paths import resolve_project_root

_logger = logging.getLogger(__name__)


def _safe_component(value: Optional[str]) -> Optional[str]:
    """Accept identifiers as path components, never arbitrary filesystem paths."""
    value = (value or "").strip()
    if not value or value in {".", ".."} or "/" in value or "\\" in value:
        return None
    return value


def _read_json(path: Path) -> Optional[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


class _TaskRunLookupError(ValueError):
    """The recorded run exists but its evidence cannot be attributed safely."""


def _merge_event(project_root: Path, plan: str, task: str) -> Optional[dict]:
    """Return the latest task-specific merge event for a plan task."""
    try:
        from snodo.infrastructure.audit import AuditLog
        events = AuditLog(str(project_root / ".snodo" / "audit.log")).get_history("task_merged")
    except Exception as exc:
        _logger.debug("Could not read task merge history: %s", exc)
        return None
    prefix = f"task/{plan}/{task}/"
    for event in reversed(events):
        data = event.data or {}
        if data.get("task_ref") == task and str(data.get("branch", "")).startswith(prefix):
            if isinstance(data.get("merge_sha"), str) and data["merge_sha"]:
                return data
    return None


def _inline_change_size(project_root: Path, merge_event: dict) -> dict:
    """Build a task-only range from the merge commit recorded in the audit log."""
    from snodo.tools.git import open_repo

    merge_sha = merge_event["merge_sha"]
    try:
        with open_repo(str(project_root)) as repo:
            merge_commit = repo.commit(merge_sha)
            if not merge_commit.parents:
                raise _TaskRunLookupError("the task merge commit has no parent")
            base_sha = merge_commit.parents[0].hexsha
            paths = repo.git.diff("--name-only", base_sha, merge_sha).splitlines()
    except _TaskRunLookupError:
        raise
    except Exception as exc:
        raise _TaskRunLookupError(
            f"could not resolve task merge commit '{merge_sha}': {exc}"
        ) from exc
    if not paths:
        raise _TaskRunLookupError("the task merge commit has no attributable diff")
    return {
        "base_sha": base_sha,
        "head_sha": merge_sha,
        "paths": sorted(set(paths)),
        "files_changed": len(set(paths)),
    }


def _inline_task_job(project_root: Path, plan: str, task: str, job_id: Optional[str], jobs_dir: Path) -> Optional[dict]:
    """Resolve a task executed inline inside a completed plan-level job."""
    merge_event = _merge_event(project_root, plan, task)
    if not merge_event:
        return None

    candidates = []
    job_dirs = [jobs_dir / job_id] if job_id else (list(jobs_dir.iterdir()) if jobs_dir.is_dir() else [])
    for job_dir in job_dirs:
        if not job_dir.is_dir():
            continue
        task_data = _read_json(job_dir / "task.json") or {}
        state = _read_json(job_dir / "state.json") or {}
        if task_data.get("plan_name") != plan:
            continue
        if task_data.get("task_id") or task_data.get("retry_task_id"):
            continue
        if str(state.get("status", "")).lower() != "completed":
            continue
        candidates.append({"state": state, "job_dir": job_dir})
    task_state = _read_json(project_root / ".snodo" / "tasks" / task / "state.json") or {}
    if candidates:
        candidates.sort(key=lambda item: item["state"].get("completed_at") or item["state"].get("created_at") or 0)
        selected = candidates[-1]
        state = dict(selected["state"])
    else:
        # A foreground plan run has no plan-level job. Its task state is the
        # run record; pair it with the merge event to attribute the exact diff.
        if str(task_state.get("status", "")).lower() != "completed":
            return None
        selected = {"state": task_state, "job_dir": None}
        state = dict(task_state)
    cost = dict(state.get("cost") or {})
    task_cost = task_state.get("cost") if isinstance(task_state.get("cost"), dict) else {}
    provenance = task_cost.get("provenance") if isinstance(task_cost.get("provenance"), dict) else {}
    if not provenance:
        provenance = cost.get("provenance") if isinstance(cost.get("provenance"), dict) else {}
    if not provenance.get("model"):
        raise _TaskRunLookupError("the inline task has no attributable model provenance")
    cost["change_size"] = _inline_change_size(project_root, merge_event)
    cost["provenance"] = provenance
    state["cost"] = cost
    selected["state"] = state
    return selected


def _task_job(project_root: Path, plan: str, task: str, job_id: Optional[str] = None) -> Optional[dict]:
    """Find the latest completed per-task job or safely resolve an inline task."""
    jobs_dir = project_root / ".snodo" / "jobs"
    candidates = []
    job_dirs = [jobs_dir / job_id] if job_id else (list(jobs_dir.iterdir()) if jobs_dir.is_dir() else [])
    for job_dir in job_dirs:
        if not job_dir.is_dir():
            continue
        task_data = _read_json(job_dir / "task.json") or {}
        state = _read_json(job_dir / "state.json") or {}
        task_id = task_data.get("task_id") or task_data.get("retry_task_id")
        if task_id != task:
            continue
        task_plan = task_data.get("task_plan") or task_data.get("plan_name")
        if not task_plan and task_data.get("parent_job"):
            parent_task = _read_json(jobs_dir / str(task_data["parent_job"]) / "task.json") or {}
            task_plan = parent_task.get("plan_name") or parent_task.get("task_plan")
        if task_plan == plan and str(state.get("status", "")).lower() == "completed":
            candidates.append({"state": state, "job_dir": job_dir})

    if not candidates:
        return _inline_task_job(project_root, plan, task, job_id, jobs_dir)
    candidates.sort(key=lambda item: item["state"].get("completed_at") or item["state"].get("created_at") or 0)
    return candidates[-1]


def _merge_sha(project_root: Path, plan: str, task: str) -> Optional[str]:
    """Return the recorded merge commit for this plan task, when available."""
    event = _merge_event(project_root, plan, task)
    return event.get("merge_sha") if event else None


def _git_diff(project_root: Path, change_size: dict, merge_sha: Optional[str] = None) -> str:
    """Read the patch from the job's recorded commits, never the operator's HEAD."""
    from snodo.tools.git import open_repo

    base = change_size.get("base_sha")
    head = change_size.get("head_sha")
    if not isinstance(base, str) or not isinstance(head, str) or not base or not head:
        raise RuntimeError("the job has no recorded base_sha/head_sha")

    repo = open_repo(str(project_root))
    try:
        ranges = [(base, head)]
        if merge_sha and merge_sha != head:
            ranges.append((base, merge_sha))
        for start, end in ranges:
            try:
                repo.commit(start)
                repo.commit(end)
                diff = repo.git.diff("--binary", start, end, "--")
            except Exception:
                continue
            if diff.strip():
                return diff
    finally:
        repo.close()
    raise RuntimeError("the recorded task commit range has no resolvable, non-empty diff")


def models_set_baseline_command(args) -> int:
    """Capture or replace a completed task's local solution baseline."""
    plan = _safe_component(getattr(args, "plan", None))
    task = _safe_component(getattr(args, "task", None) or getattr(args, "task_id", None))
    json_out = getattr(args, "json", False)

    def fail(message: str) -> int:
        if json_out:
            return emit_error("models-set-baseline", message, 1)
        print(f"Error: {message}", file=sys.stderr)
        return 1

    if not plan or not task:
        return fail("--set-baseline requires --plan and --task")
    root_value = resolve_project_root()
    if root_value is None:
        return fail("Not inside a snodo project.")
    root = Path(root_value)
    if not (root / ".snodo" / "plans" / plan).is_dir():
        return fail(f"Plan '{plan}' not found.")

    try:
        job = _task_job(root, plan, task)
    except _TaskRunLookupError as exc:
        return fail(f"Could not attribute the recorded run for task '{plan}/{task}': {exc}.")
    if not job:
        return fail(f"Could not locate the recorded run for task '{plan}/{task}'.")
    state = job["state"]
    if str(state.get("status", "")).lower() != "completed":
        return fail(f"Recorded run for task '{plan}/{task}' is not completed.")
    cost = state.get("cost") if isinstance(state.get("cost"), dict) else {}
    change_size = cost.get("change_size") if isinstance(cost.get("change_size"), dict) else None
    if not change_size:
        return fail(f"Recorded run for task '{plan}/{task}' has no change-size commit range.")

    try:
        diff = _git_diff(root, change_size, _merge_sha(root, plan, task))
    except Exception as exc:
        _logger.debug("Could not capture baseline git snapshot: %s", exc)
        return fail(f"Could not capture task diff: {exc}")

    provenance = cost.get("provenance") if isinstance(cost.get("provenance"), dict) else {}
    validators = state.get("halt", {}).get("validator_results", []) if isinstance(state.get("halt"), dict) else []
    if not isinstance(validators, list):
        validators = []
    baseline_dir = root / ".snodo" / "baselines" / plan / task
    baseline_dir.mkdir(parents=True, exist_ok=True)
    diff_path = baseline_dir / "solution.diff"
    diff_path.write_text(diff, encoding="utf-8")
    record = {
        "schema": "snodo.task-baseline.v1",
        "plan": plan,
        "task": task,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "model": provenance.get("model"),
        "coder": provenance.get("coder"),
        "status": state.get("status"),
        "validator_results": validators,
        "change_size": change_size,
        "diff_file": str(diff_path.relative_to(root)),
        "diff_sha256": hashlib.sha256(diff.encode("utf-8")).hexdigest(),
    }
    record_path = baseline_dir / "baseline.json"
    record_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    if json_out:
        return emit_json({"schema": schema_name("models-set-baseline"), "ok": True, **record})
    print(f"Captured baseline for {plan}/{task}.")
    print(f"  model: {record['model'] or 'unknown'}")
    print(f"  diff:  {record['diff_file']}")
    return 0

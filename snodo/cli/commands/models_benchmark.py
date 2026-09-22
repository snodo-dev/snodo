"""Run a stored task baseline again with another model."""

import json
import secrets
import sys
from pathlib import Path

import yaml
from snodo.cli.json_output import emit_error
from snodo.infrastructure.paths import resolve_project_root


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Could not read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return value


def _safe(value: object, label: str) -> str:
    text = str(value or "").strip()
    if not text or text in {".", ".."} or "/" in text or "\\" in text:
        raise ValueError(f"Invalid {label}")
    return text


def _task_spec(root: Path, plan: str, task: str) -> str:
    plan_dir = root / ".snodo" / "plans" / plan
    try:
        plan_data = yaml.safe_load((plan_dir / "plan.yml").read_text(encoding="utf-8")) or {}
    except ValueError:
        plan_data = {}
    for wave in plan_data.get("waves", []):
        if task not in [str(item) for item in wave.get("tasks", [])]:
            continue
        spec_path = plan_dir / f"wave_{wave.get('id')}" / f"{task}_task.md"
        if spec_path.is_file():
            return spec_path.read_text(encoding="utf-8")

    jobs_dir = root / ".snodo" / "jobs"
    matches = []
    for job_dir in jobs_dir.glob("*/"):
        task_path = job_dir / "task.json"
        state_path = job_dir / "state.json"
        if not task_path.is_file():
            continue
        data = _read_json(task_path)
        if data.get("task_id") != task and data.get("retry_task_id") != task:
            continue
        if data.get("task_plan") != plan and data.get("plan_name") != plan:
            continue
        state = _read_json(state_path) if state_path.is_file() else {}
        matches.append((state.get("completed_at") or state.get("created_at") or 0, data))
    if matches:
        matches.sort(key=lambda item: item[0])
        description = matches[-1][1].get("description")
        if description:
            return str(description)
    raise ValueError(f"Could not locate the task spec for '{plan}/{task}'")


def models_benchmark_run_command(args) -> int:
    """Dispatch a candidate from the baseline's recorded base commit."""
    json_out = bool(getattr(args, "json", False))
    try:
        plan = _safe(getattr(args, "plan", None), "plan")
        task = _safe(getattr(args, "task", None), "task")
        model = str(getattr(args, "model", None) or "").strip()
        if not model:
            raise ValueError("--benchmark-run requires --model")
        root_value = resolve_project_root()
        if root_value is None:
            raise ValueError("Not inside a snodo project.")
        root = Path(root_value)
        baseline_dir = root / ".snodo" / "baselines" / plan / task
        baseline = _read_json(baseline_dir / "baseline.json")
        if baseline.get("schema") != "snodo.task-baseline.v1":
            raise ValueError(f"Unsupported baseline schema in {baseline_dir / 'baseline.json'}")
        base = (baseline.get("change_size") or {}).get("base_sha")
        if not base:
            raise ValueError("Baseline has no change_size.base_sha")
        description = _task_spec(root, plan, task)
        nonce = secrets.token_hex(6)
        branch = f"benchmark/{plan}/{task}/{nonce}"
        task_args = {
            "task_id": task,
            "description": description,
            "protocol": getattr(args, "protocol", None) or ".snodo/protocol.yml",
            "model": model,
            "cwd": str(root),
            "benchmark": True,
            "retain_worktree": True,
            "base": base,
            "branch": branch,
            # This scopes only the disposable worktree identity; benchmark
            # execution does not write plan status or task records.
            "task_plan": f"benchmark-{nonce}",
        }
        from snodo.jobs import JobManager
        manager = JobManager(str(root))
        job_id = manager.submit(task_args)
        final = manager.wait_for(job_id)
        from snodo.cli.commands.plan_compare import compare_models_command
        compare_args = type("CompareArgs", (), {
            "plan": plan, "task": task, "job": job_id, "json": json_out,
            "benchmark_branch": branch, "benchmark_base": base,
            "benchmark_status": final.get("status"),
        })()
        if json_out:
            # compare_models_command emits its own JSON document; include the
            # run identity there rather than producing two JSON values.
            return compare_models_command(compare_args)
        return compare_models_command(compare_args)
    except (OSError, ValueError, RuntimeError) as exc:
        if json_out:
            return emit_error("models-benchmark-run", str(exc), 1)
        print(f"Error: {exc}", file=sys.stderr)
        return 1

"""Compare a recorded candidate job with a stored task baseline."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

_OUTCOME_RANK = {
    "pass": 1, "completed": 1, "warn": 0, "escalate": 0,
    "blocker": -1, "failed": -1, "validator_error": -1,
    "internal_error": -1, "environment_error": -1,
}


def _read_json(path: Path, label: str) -> dict:
    if not path.is_file():
        raise ValueError(f"{label} not found: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Could not read {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object: {path}")
    return value


def _baseline(project_root: Path, plan: str, task: str) -> tuple[dict, Path]:
    directory = project_root / ".snodo" / "baselines" / plan / task
    record_path = directory / "baseline.json"
    record = _read_json(record_path, "Baseline")
    if record.get("schema") != "snodo.task-baseline.v1":
        raise ValueError(f"Unsupported baseline schema in {record_path}")
    solution = directory / "solution.diff"
    if not solution.is_file():
        raise ValueError(f"Baseline patch not found: {solution}")
    return record, solution


def _benchmark_candidate(project_root: Path, plan: str, task: str, job_dir: Path) -> Optional[dict]:
    """Resolve a completed benchmark job using its own task metadata and branch."""
    task_data = _read_json(job_dir / "task.json", "Job task record")
    state = _read_json(job_dir / "state.json", "Job state")
    branch = task_data.get("branch")
    base = task_data.get("base")
    task_id = task_data.get("task_id") or task_data.get("retry_task_id")
    task_plan = task_data.get("task_plan")
    if (task_id != task or not isinstance(task_plan, str) or not task_plan.startswith("benchmark-")
            or not isinstance(branch, str) or branch != f"benchmark/{plan}/{task}/{branch.rsplit('/', 1)[-1]}"
            or not base or str(state.get("status", "")).lower() != "completed"):
        return None
    model = task_data.get("model")
    if not isinstance(model, str) or not model.strip():
        raise ValueError(f"Benchmark job '{job_dir.name}' has no model in task.json")
    try:
        head = subprocess.run(  # noqa: S603 - fixed git subcommand; branch is validated evidence
            ["git", "-C", str(project_root), "rev-parse", "--verify", f"{branch}^{{commit}}"],  # noqa: S607 - git is the required repository tool
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        paths = subprocess.run(  # noqa: S603 - fixed git subcommand; SHAs come from job evidence
            ["git", "-C", str(project_root), "diff", "--name-only", str(base), head],  # noqa: S607 - git is the required repository tool
            capture_output=True, text=True, check=True,
        ).stdout.splitlines()
        numstat = subprocess.run(  # noqa: S603 - fixed git subcommand; SHAs come from job evidence
            ["git", "-C", str(project_root), "diff", "--numstat", str(base), head],  # noqa: S607 - git is the required repository tool
            capture_output=True, text=True, check=True,
        ).stdout.splitlines()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError(f"Could not resolve benchmark job '{job_dir.name}' range: {exc}") from exc
    added = deleted = 0
    for line in numstat:
        fields = line.split("\t", 2)
        if len(fields) == 3:
            try:
                added += int(fields[0])
                deleted += int(fields[1])
            except ValueError:  # binary file counts are reported as '-'
                continue
    result = dict(state)
    cost = dict(result.get("cost") or {})
    cost["change_size"] = {
        "base_sha": str(base), "head_sha": head, "paths": sorted(set(paths)),
        "files_changed": len(set(paths)), "lines_added": added, "lines_deleted": deleted,
    }
    result["cost"] = cost
    result["model"] = model
    result["branch"] = branch
    return {"state": result, "job_dir": job_dir}


def _job_record(project_root: Path, plan: str, task: str, job_id: Optional[str]) -> tuple[dict, Path]:
    jobs_dir = project_root / ".snodo" / "jobs"
    job_dirs = [jobs_dir / job_id] if job_id else (list(jobs_dir.iterdir()) if jobs_dir.is_dir() else [])
    candidates = []
    for job_dir in job_dirs:
        if not job_dir.is_dir():
            continue
        candidate = _benchmark_candidate(project_root, plan, task, job_dir)
        if candidate:
            candidates.append(candidate)
    if not candidates:
        if job_id:
            raise ValueError(f"Job '{job_id}' is not a completed benchmark candidate for plan '{plan}' and task '{task}'")
        raise ValueError(f"Benchmark candidate job evidence not found for plan '{plan}' and task '{task}'")
    candidates.sort(key=lambda item: item["state"].get("completed_at") or item["state"].get("created_at") or 0)
    job = candidates[-1]
    return job["state"], job["job_dir"] / "state.json"


def _change_size(record: dict) -> dict:
    size = record.get("change_size")
    if not isinstance(size, dict):
        size = (record.get("cost") or {}).get("change_size")
    return size if isinstance(size, dict) else {}


def _paths(record: dict) -> set[str]:
    paths = _change_size(record).get("paths") or record.get("paths") or []
    return {str(path) for path in paths if path is not None}


def _outcome(record: dict) -> str:
    halt = record.get("halt") if isinstance(record.get("halt"), dict) else {}
    explicit = record.get("status") or halt.get("final_decision") or halt.get("halt_type")
    if explicit == "completed":
        explicit = "pass"
    results = record.get("validator_results") or halt.get("validator_results") or []
    severities = {str(item.get("severity", "")).lower() for item in results if isinstance(item, dict)}
    if "blocker" in severities:
        return "blocker"
    if "warn" in severities:
        return "warn"
    return str(explicit or "unknown").lower()


def _diff_lines(diff: str) -> set[tuple[str, str, str]]:
    """Extract content-addressable changed lines from a unified diff."""
    result: set[tuple[str, str, str]] = set()
    path = None
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            path = line[6:]
        elif path and line.startswith(("+", "-")) and not line.startswith(("+++", "---")):
            result.add((path, line[0], line[1:]))
    return result


def _candidate_patch(project_root: Path, size: dict) -> str:
    base, head = size.get("base_sha"), size.get("head_sha")
    if not base or not head:
        raise ValueError("Candidate change_size must include base_sha and head_sha")
    try:
        result = subprocess.run(  # noqa: S603 - fixed git subcommand; SHAs are evidence fields
            ["git", "-C", str(project_root), "diff", "--no-ext-diff", str(base), str(head)],  # noqa: S607 - git is the required repository tool
            capture_output=True, text=True, check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError(f"Could not read candidate patch for {base}..{head}: {exc}") from exc
    return result.stdout


def _number(value: Any) -> float:
    return float(value) if isinstance(value, (int, float)) else 0.0


def _direction(value: float) -> str:
    return "better" if value > 0 else "worse" if value < 0 else "same"


def compare_evidence(baseline: dict, baseline_diff: str, candidate: dict, candidate_diff: str) -> dict:
    """Build explainable comparison signals from baseline and candidate evidence."""
    baseline_lines, candidate_lines = _diff_lines(baseline_diff), _diff_lines(candidate_diff)
    intersection = baseline_lines & candidate_lines
    precision = len(intersection) / len(candidate_lines) if candidate_lines else 0.0
    recall = len(intersection) / len(baseline_lines) if baseline_lines else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    base_paths, candidate_paths = _paths(baseline), _paths(candidate)
    overlap, extra = base_paths & candidate_paths, candidate_paths - base_paths
    coverage = len(overlap) / len(base_paths) if base_paths else (1.0 if not candidate_paths else 0.0)
    extra_ratio = len(extra) / len(base_paths) if base_paths else (1.0 if extra else 0.0)
    base_size, candidate_size = _change_size(baseline), _change_size(candidate)
    base_units = _number(base_size.get("lines_added")) + _number(base_size.get("lines_deleted"))
    candidate_units = _number(candidate_size.get("lines_added")) + _number(candidate_size.get("lines_deleted"))
    baseline_outcome, candidate_outcome = _outcome(baseline), _outcome(candidate)
    outcome_delta = 10.0 * (_OUTCOME_RANK.get(candidate_outcome, 0) - _OUTCOME_RANK.get(baseline_outcome, 0))
    # Content is intentionally dominant: it is the closest available measure
    # of whether the candidate made the same useful edits as the baseline.
    content_delta = 70.0 * (f1 - 1.0)
    surface_delta = max(-20.0, min(20.0, 10.0 * (coverage - 1.0) - 10.0 * extra_ratio))
    signals = {
        "content": {
            "baseline_lines": len(baseline_lines), "candidate_lines": len(candidate_lines),
            "matched_lines": len(intersection), "precision": round(precision, 4),
            "recall": round(recall, 4), "f1": round(f1, 4),
            "delta": round(content_delta, 2), "direction": _direction(content_delta),
        },
        "validator_outcome": {
            "baseline": baseline_outcome, "candidate": candidate_outcome,
            "delta": round(outcome_delta, 2), "direction": _direction(outcome_delta),
        },
        "changed_surface": {
            "baseline_files": len(base_paths), "candidate_files": len(candidate_paths),
            "overlap_files": len(overlap), "coverage_pct": round(coverage * 100, 2),
            "extra_files": len(extra), "extra_paths": sorted(extra),
            "delta": round(surface_delta, 2), "direction": _direction(surface_delta),
        },
        "change_size": {
            "baseline_lines": base_units, "candidate_lines": candidate_units,
            "difference_lines": candidate_units - base_units,
            "baseline_files": len(base_paths), "candidate_files": len(candidate_paths),
            "scored": False,
        },
    }
    score = round(content_delta + outcome_delta + surface_delta, 2)
    return {
        "direction": _direction(score), "delta_score": score, "signals": signals,
        "models": {
            "baseline": baseline.get("model"),
            "candidate": (candidate.get("cost") or {}).get("provenance", {}).get("model") or candidate.get("model"),
        },
        "paths": {"baseline": sorted(base_paths), "candidate": sorted(candidate_paths), "overlap": sorted(overlap)},
    }


def compare_models_command(args) -> int:
    """Compare a candidate job against ``.snodo/baselines/<plan>/<task>``."""
    json_out = bool(getattr(args, "json", False))
    try:
        from snodo.infrastructure.paths import resolve_project_root
        root = resolve_project_root()
        if root is None:
            raise ValueError("Not inside a snodo project.")
        plan, task = getattr(args, "plan", None), getattr(args, "task", None)
        if not plan or not task:
            raise ValueError("--compare requires --plan and --task")
        baseline, baseline_path = _baseline(Path(root), plan, task)
        if baseline.get("plan") not in (None, plan) or baseline.get("task") not in (None, task):
            raise ValueError("Baseline plan/task does not match the requested --plan/--task")
        candidate, candidate_path = _job_record(Path(root), plan, task, getattr(args, "job", None))
        candidate_diff = _candidate_patch(Path(root), _change_size(candidate))
        expected_sha = baseline.get("diff_sha256")
        actual_sha = hashlib.sha256(baseline_path.with_name("solution.diff").read_bytes()).hexdigest()
        if expected_sha and expected_sha != actual_sha:
            raise ValueError("Baseline solution.diff does not match baseline diff_sha256")
        result = compare_evidence(
            baseline, baseline_path.with_name("solution.diff").read_text(encoding="utf-8"),
            candidate, candidate_diff,
        )
        result.update({"plan": plan, "task": task, "job": candidate_path.parent.name,
                       "baseline": str(baseline_path.parent),
                       "candidate_patch_sha256": hashlib.sha256(candidate_diff.encode()).hexdigest()})
        benchmark_branch = candidate.get("branch") or getattr(args, "benchmark_branch", None)
        if benchmark_branch:
            result["benchmark"] = {
                "branch": benchmark_branch,
                "base": _change_size(candidate).get("base_sha"),
                "status": getattr(args, "benchmark_status", None) or candidate.get("status"),
            }
    except (OSError, ValueError) as exc:
        if json_out:
            from snodo.cli.json_output import emit_error
            return emit_error("models-compare", str(exc), 1)
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    if json_out:
        from snodo.cli.json_output import emit_json, schema_name
        return emit_json({"schema": schema_name("models-compare"), "ok": True, **result})
    print(f"Candidate comparison: {result['delta_score']:+.0f} vs baseline ({result['direction']})")
    if "benchmark" in result:
        benchmark = result["benchmark"]
        print(f"  benchmark job:     {result['job']}")
        print(f"  benchmark branch:  {benchmark['branch']}")
        print(f"  benchmark base:    {benchmark['base']}")
        print(f"  benchmark status:  {benchmark['status']}")
    print(f"  models:            {result['models']['baseline']} -> {result['models']['candidate']}")
    content = result["signals"]["content"]
    print(f"  content:           F1 {content['f1']:.2f}, precision {content['precision']:.2f}, recall {content['recall']:.2f} ({content['direction']})")
    outcome = result["signals"]["validator_outcome"]
    print(f"  validator outcome: {outcome['baseline']} -> {outcome['candidate']} ({outcome['direction']})")
    surface = result["signals"]["changed_surface"]
    print(f"  changed surface:   {surface['overlap_files']}/{surface['baseline_files']} files covered; {surface['extra_files']} extra ({surface['direction']})")
    size = result["signals"]["change_size"]
    print(f"  change size:       {size['baseline_lines']:g} -> {size['candidate_lines']:g} lines ({size['difference_lines']:+g}; not scored)")
    return 0

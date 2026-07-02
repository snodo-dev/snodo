"""EXP1-RUN: Enforcement ablation runner.

Runs the frozen task set through 3 arms and scores with the SWE-bench
oracle.  All knobs come from experiments/config.py; results are committed;
stats are NOT computed here (EXP-REPORT owns analysis).

Agent cells are dispatched via ProcessPoolExecutor (process isolation) so
arm-c's in-process chdir does not race with other cells.  Mock mode stays
serial (no process overhead needed for in-memory mocks).

Invocation:
    uv run python -m experiments.run_exp1          # full matrix
    uv run python -m experiments.run_exp1 --smoke   # 1 task, mocks

Real execution requires Docker + swebench + opencode + model API keys.
"""

from __future__ import annotations

import argparse
import json
from concurrent.futures import ProcessPoolExecutor, as_completed
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from experiments.arms.arm_a_opencode import MockArmA
from experiments.arms.arm_a_opencode import run as run_arm_a
from experiments.arms.arm_b_prose import MockArmB
from experiments.arms.arm_b_prose import run as run_arm_b
from experiments.arms.arm_c_snodo import MockArmC
from experiments.arms.arm_c_snodo import run as run_arm_c
from experiments.arms.prose import protocol_to_prose
from experiments.config import load_config, write_snapshot
from experiments.scoring import RealScorer, MockScorer, make_scorer
from experiments.workspace import MockWorkspace, setup_instance_workspace, teardown

_HERE = Path(__file__).resolve().parent
_DEFAULT_SELECTION = _HERE / "tasks" / "selection.jsonl"
_DEFAULT_RESULTS = _HERE / "results" / "exp1"
_ARMS = ["a", "b", "c"]

# ---------------------------------------------------------------------------
# Protocol loader (shared by parity gate + process-pool workers)
# ---------------------------------------------------------------------------

_EXP_PROTOCOL = None


def _load_protocol() -> Any:
    """Load the intent-driven protocol template.  Cache after first load.

    Uses the `intent` template: no hard pre-execute spec gates (so externally
    authored problem statements aren't blocked before the coder runs), and a
    post-execute `review` validator (with read_diff_between_refs) that judges
    the produced diff. arm-b renders this same protocol as prose.
    """
    global _EXP_PROTOCOL
    if _EXP_PROTOCOL is not None:
        return _EXP_PROTOCOL

    import yaml
    from snodo.compiler.models import Protocol

    import snodo.protocols

    template_path = Path(snodo.protocols.__file__).parent / "templates" / "intent.yml"
    data = yaml.safe_load(template_path.read_text())
    _EXP_PROTOCOL = Protocol(**data)
    return _EXP_PROTOCOL


# ---------------------------------------------------------------------------
# Process-pool worker — each call is a standalone subprocess (own cwd / state)
# ---------------------------------------------------------------------------


def _run_one_cell(
    task_json: str,
    arm: str,
    trial_id: int,
    config_json: str,
    run_id: str,
    prose: str,
) -> str:
    """Run one (task, arm, trial) cell in a worker subprocess.

    Each worker has its own interpreter state and cwd — arm-c's chdir
    cannot race.  Setup workspace -> dispatch arm -> teardown -> return
    result.  Arguments are JSON-serialized for cross-process safety.
    Mock mode uses serial dispatch (never reaches the pool).

    Returns:
        JSON-encoded result dict with keys: patch, wall_s, cost_usd,
        closure_json, error.
    """
    import time as _time

    task = json.loads(task_json)
    config = json.loads(config_json)
    start = _time.monotonic()

    try:
        workspace = setup_instance_workspace(task)
    except Exception as exc:
        return json.dumps({"patch": "", "wall_s": _time.monotonic() - start, "cost_usd": None, "closure_json": None, "error": f"workspace_setup_failed: {exc}"})

    protocol = _load_protocol_for_worker() if arm == "c" else None
    try:
        result = _dispatch_arm(arm, task, config, run_id, trial_id, prose=prose, protocol=protocol, workspace=workspace)
    finally:
        teardown(workspace)

    return json.dumps({
        "patch": result.get("patch", ""),
        "wall_s": result.get("wall_s", 0.0),
        "cost_usd": result.get("cost_usd"),
        "closure_json": result.get("closure_json"),
        "error": result.get("error"),
    })


def _load_protocol_for_worker() -> Any:
    """Load protocol inside a worker process (no global cache)."""
    import yaml as _yaml
    from snodo.compiler.models import Protocol as _Protocol
    import snodo.protocols as _protos
    _path = Path(_protos.__file__).parent / "templates" / "intent.yml"
    _data = _yaml.safe_load(_path.read_text())
    return _Protocol(**_data)


# ---------------------------------------------------------------------------
# Results helpers
# ---------------------------------------------------------------------------

_RESULTS_KEYS = [
    "instance_id",
    "arm",
    "trial_id",
    "run_id",
    "base_model",
    "temperature",
    "model_name_or_path",
    "resolved",
    "n_fail_to_pass_passed",
    "regressions",
    "wall_s",
    "cost_usd",
    "closure_json",
    "exclusion_reason",
    "error",
]


def _make_result_row(
    instance_id: str,
    arm: str,
    trial_id: int,
    run_id: str,
    config: dict,
    arm_result: dict,
    score_result: dict,
    exclusion_reason: Optional[str] = None,
) -> dict:
    model = config["models"]["reference"]
    temp = config["sampling"]["temperature"]
    return {
        "instance_id": instance_id,
        "arm": arm,
        "trial_id": trial_id,
        "run_id": run_id,
        "base_model": model,
        "temperature": temp,
        "model_name_or_path": f"exp1-{arm}-{instance_id}-{run_id}-t{trial_id}",
        "resolved": score_result.get("resolved", False),
        "n_fail_to_pass_passed": score_result.get("n_fail_to_pass_passed", 0),
        "regressions": score_result.get("regressions", 0),
        "wall_s": arm_result.get("wall_s", 0.0),
        "cost_usd": arm_result.get("cost_usd"),
        "closure_json": arm_result.get("closure_json"),
        "patch_len": len(arm_result.get("patch") or ""),
        "patch_preview": (arm_result.get("patch") or "")[:400],
        "exclusion_reason": exclusion_reason,
        "error": arm_result.get("error"),
        "data": {
            "experiment": "exp1",
            "run_id": run_id,
        },
    }


def _load_existing_results(results_path: Path) -> List[dict]:
    """Load existing results for idempotency check."""
    if not results_path.exists():
        return []
    rows = []
    with open(results_path) as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _write_results_row(results_path: Path, row: dict) -> None:
    """Append a single result row to results.jsonl."""
    results_path.parent.mkdir(parents=True, exist_ok=True)
    with open(results_path, "a") as f:
        f.write(json.dumps(row, default=str) + "\n")


def _row_completed(existing: List[dict], instance_id: str, arm: str, trial_id: int) -> bool:
    """Check if a (task, arm, trial) has already been completed."""
    for r in existing:
        if (
            r.get("instance_id") == instance_id
            and r.get("arm") == arm
            and r.get("trial_id") == trial_id
        ):
            return True
    return False


# ---------------------------------------------------------------------------
# Positive control
# ---------------------------------------------------------------------------

def _positive_control(
    task: dict,
    scorer: Any,
) -> tuple[bool, Optional[str]]:
    """Run the task's reference (gold) patch through the scorer.

    Checks ``gold_patch`` first, then falls back to ``patch`` for raw
    SWE-bench fixture rows.  Uses the gold cache (RealScorer) to avoid
    re-running the harness for the same deterministic result.

    Returns (passes, exclusion_reason).
    """
    gold_patch = task.get("gold_patch") or task.get("patch", "")
    if not gold_patch:
        return False, "no_gold_patch"

    if isinstance(scorer, RealScorer):
        result = scorer.score_gold_with_cache(task)
    else:
        result = scorer.score(task, gold_patch, "gold-baseline")

    if not result.get("resolved", False):
        err = result.get("error")
        reason = "harness_broken" if not err else f"harness_broken: {err}"
        return False, reason
    return True, None


# ---------------------------------------------------------------------------
# Arm dispatch (serial mock path)
# ---------------------------------------------------------------------------

def _dispatch_arm(
    arm: str,
    task: dict,
    config: dict,
    run_id: str,
    trial_id: int,
    prose: str = "",
    protocol: Any = None,
    workspace: Any = None,
    mock: bool = False,
) -> dict:
    """Dispatch a single arm run and return the arm result."""
    if mock:
        if arm == "a":
            return MockArmA().run(task, config, run_id, trial_id, workspace=workspace)
        elif arm == "b":
            return MockArmB().run(task, config, run_id, trial_id, prose=prose, workspace=workspace)
        elif arm == "c":
            return MockArmC().run(task, config, run_id, trial_id, protocol=protocol, workspace=workspace)
    else:
        if arm == "a":
            return run_arm_a(task, config, run_id, trial_id, workspace=workspace)
        elif arm == "b":
            return run_arm_b(task, config, run_id, trial_id, prose=prose, workspace=workspace)
        elif arm == "c":
            return run_arm_c(task, config, run_id, trial_id, protocol=protocol, workspace=workspace)
    return {"patch": "", "wall_s": 0.0, "cost_usd": None, "error": f"unknown arm: {arm}"}


# ---------------------------------------------------------------------------
# Process-pool worker — full per-task workflow (positive control + cells + score)
# ---------------------------------------------------------------------------


def _run_one_task(
    task_json: str,
    config_json: str,
    run_id: str,
    arms: List[str],
    prose: str,
    mock: bool,
    scorer_override: Any = None,
) -> str:
    """Run the complete per-task workflow in a worker subprocess.

    Positive control -> dispatch all (arm, trial) cells (serial within task) ->
    batch score all predictions.  Returns a JSON list of result rows.

    In mock mode (no process pool), *scorer_override* (e.g. a MockScorer) can
    be passed for test gold-scoring.  In real mode scoring runs via the
    swebench harness inside the worker so image pulls overlap across tasks.
    """
    import time as _time

    task = json.loads(task_json)
    config = json.loads(config_json)
    instance_id = task.get("instance_id", "?")
    rows: list = []

    # --- Positive control ---
    gold_patch = task.get("gold_patch") or task.get("patch", "")
    if gold_patch:
        if mock:
            # Mock mode: use the injected scorer (or a default MockScorer)
            from experiments.scoring import MockScorer as _MockScorer
            _scorer = scorer_override if scorer_override is not None else _MockScorer()
            gold_result = _scorer.score(task, gold_patch, "gold-baseline")
            gold_ok = gold_result.get("resolved", False)
            gold_error = gold_result.get("error")
        else:
            from experiments.scoring import _cached_gold_result, _set_cached_gold_result, score_prediction
            cached = _cached_gold_result(task)
            if cached is not None:
                gold_ok = cached.get("resolved", False)
                gold_error = cached.get("error")
            else:
                gold_result = score_prediction(task, gold_patch, "gold-baseline")
                _set_cached_gold_result(task, gold_result)
                gold_ok = gold_result.get("resolved", False)
                gold_error = gold_result.get("error")

        if not gold_ok:
            exclusion_row = _make_result_row(
                instance_id, "positive_control", 0, run_id, config,
                {"patch": gold_patch, "wall_s": 0.0, "cost_usd": None, "error": None},
                {"resolved": False},
                exclusion_reason="harness_broken" if not gold_error else f"harness_broken: {gold_error}",
            )
            return json.dumps([exclusion_row])

    # --- Dispatch all (arm, trial) cells ---
    from experiments.workspace import MockWorkspace, teardown
    ws_manager = MockWorkspace() if mock else None

    pending: list = []
    for arm in arms:
        for trial_id in range(1, config["sampling"]["k_trials"] + 1):
            if mock:
                start = _time.monotonic()
                try:
                    workspace = ws_manager.setup(task)
                except Exception as exc:
                    pending.append((arm, trial_id, {"patch": "", "wall_s": _time.monotonic() - start, "cost_usd": None, "closure_json": None, "error": f"workspace_setup_failed: {exc}"}))
                    continue
                try:
                    result = _dispatch_arm(arm, task, config, run_id, trial_id, prose=prose, protocol=None, workspace=workspace, mock=True)
                finally:
                    teardown(workspace)
                pending.append((arm, trial_id, result))
            else:
                cell_json = _run_one_cell(task_json, arm, trial_id, config_json, run_id, prose)
                cell = json.loads(cell_json)
                pending.append((arm, trial_id, cell))

    # --- Batch score all predictions ---
    from experiments.scoring import score_predictions_batch
    max_workers = config.get("bounds", {}).get("scoring", {}).get("max_workers", 1)
    ns = config.get("bounds", {}).get("scoring", {}).get("namespace", "swebench")
    cl = config.get("bounds", {}).get("scoring", {}).get("cache_level", "instance")
    batch_input = [
        (task, r.get("patch", ""), f"exp1-{arm}-{instance_id}-t{tid}")
        for arm, tid, r in pending
    ]

    if mock:
        from experiments.scoring import MockScorer as _MockScorer
        _batch_scorer = scorer_override if scorer_override is not None else _MockScorer()
        batch_results = _batch_scorer.score_batch(batch_input)
    else:
        batch_results = score_predictions_batch(batch_input, max_workers=max_workers, namespace=ns, cache_level=cl)

    for (arm, trial_id, arm_result) in pending:
        model_name = f"exp1-{arm}-{instance_id}-t{trial_id}"
        safe_name = model_name.replace("/", "__")
        score_result = batch_results.get(
            (instance_id, safe_name),
            batch_results.get((instance_id, model_name), {"resolved": False, "n_fail_to_pass_passed": 0, "regressions": 0, "error": "missing_from_batch"}),
        )
        row = _make_result_row(instance_id, arm, trial_id, run_id, config, arm_result, score_result)
        rows.append(row)

    return json.dumps(rows)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_exp1(
    config: Optional[dict] = None,
    selection_path: Optional[Path] = None,
    results_dir: Optional[Path] = None,
    arms: Optional[List[str]] = None,
    force: bool = False,
    smoke: bool = False,
    mock: bool = False,
    tasks_override: Optional[List[dict]] = None,
    scorer_override: Any = None,
    limit: Optional[int] = None,
    instance_id: Optional[str] = None,
) -> List[dict]:
    """Run EXP1 matrix: tasks x arms x trials.

    Mock mode runs serially (in-process, no process overhead).  Real mode
    dispatches TASKS concurrently via ProcessPoolExecutor so per-task
    scoring image pulls overlap.  Within each task, (arm, trial) cells run
    serially (the bottleneck is scoring, not dispatch).

    Args:
        config: Resolved config dict (loaded from config.yml if None).
        selection_path: Path to selection.jsonl.
        results_dir: Output directory for results.
        arms: List of arm names to run (default: ["a", "b", "c"]).
        force: Re-run even if already completed.
        smoke: Run only 1 task, use mocks.
        mock: Use mock arms and mock scorer.
        scorer_override: Inject a custom scorer (for testing).
        tasks_override: Optional pre-loaded task list (for testing).
        limit: Max number of tasks to process.
        instance_id: Run only this specific instance (bypasses smoke/limit).

    Returns:
        List of result rows written.
    """
    if config is None:
        config = load_config()
    if selection_path is None:
        selection_path = _DEFAULT_SELECTION
    if results_dir is None:
        results_dir = _DEFAULT_RESULTS
    if arms is None:
        arms = list(_ARMS)

    run_id = datetime.now(timezone.utc).strftime("exp1-%Y%m%d-%H%M%S-%f")

    # Load tasks
    tasks = tasks_override if tasks_override is not None else _load_tasks(selection_path)

    # Filter by --instance
    if instance_id is not None:
        tasks = [t for t in tasks if t.get("instance_id") == instance_id]
        if not tasks:
            raise ValueError(f"Instance {instance_id!r} not found in selection")

    # Filter by --limit (applied before --smoke so --limit takes precedence)
    if limit is not None and len(tasks) > limit:
        tasks = tasks[:limit]

    if smoke and len(tasks) > 1:
        tasks = tasks[:1]

    # Parity gate: load protocol and generate prose
    protocol = _load_protocol()
    prose = protocol_to_prose(protocol)
    _parity_gate(protocol, prose)

    # Results file + idempotency
    results_path = results_dir / "results.jsonl"
    existing = _load_existing_results(results_path)
    rows_written: List[dict] = []

    # Filter tasks that need work
    k_trials = config["sampling"]["k_trials"]

    def _all_cells_completed(iid: str) -> bool:
        for a in arms:
            for t in range(1, k_trials + 1):
                if not _row_completed(existing, iid, a, t):
                    return False
        return True

    tasks_to_run = [t for t in tasks if force or not _all_cells_completed(t.get("instance_id", "?"))]

    if mock:
        # Serial mock mode (in-process, no pool overhead for fast mocks)
        for task in tasks_to_run:
            task_json = json.dumps(task)
            config_json = json.dumps(config)
            task_rows = json.loads(_run_one_task(task_json, config_json, run_id, arms, prose, True, scorer_override=scorer_override))
            for row in task_rows:
                rows_written.append(row)
                _write_results_row(results_path, row)
    else:
        # Real mode: concurrent tasks via process pool
        max_parallel = config.get("bounds", {}).get("dispatch", {}).get("max_parallel", 4)
        n_workers = min(max_parallel, len(tasks_to_run)) if tasks_to_run else 1
        config_json = json.dumps(config)

        with ProcessPoolExecutor(max_workers=n_workers) as pool:
            futures = {
                pool.submit(_run_one_task, json.dumps(task), config_json, run_id, arms, prose, False): idx
                for idx, task in enumerate(tasks_to_run)
            }

            for future in as_completed(futures):
                idx = futures[future]
                task = tasks_to_run[idx]
                try:
                    task_rows = json.loads(future.result())
                except Exception as exc:
                    task_rows = [{
                        "instance_id": task.get("instance_id", "?"),
                        "arm": "error",
                        "trial_id": 0,
                        "run_id": run_id,
                        "base_model": config["models"]["reference"],
                        "temperature": config["sampling"]["temperature"],
                        "model_name_or_path": "",
                        "resolved": False,
                        "n_fail_to_pass_passed": 0,
                        "regressions": 0,
                        "wall_s": 0.0,
                        "cost_usd": None,
                        "closure_json": None,
                        "patch_len": 0,
                        "patch_preview": "",
                        "exclusion_reason": None,
                        "error": f"task worker failed: {exc}",
                        "data": {"experiment": "exp1", "run_id": run_id},
                    }]

                for row in task_rows:
                    rows_written.append(row)
                    _write_results_row(results_path, row)

    # Snapshot config for provenance
    snapshot_cfg = deepcopy(config)
    snapshot_cfg["_run_id"] = run_id
    snapshot_cfg["_arms"] = arms
    write_snapshot(results_dir, snapshot_cfg)

    return rows_written


def _load_tasks(selection_path: Path) -> List[dict]:
    """Load tasks from selection.jsonl."""
    tasks = []
    with open(selection_path) as f:
        for line in f:
            line = line.strip()
            if line:
                tasks.append(json.loads(line))
    return tasks


def _parity_gate(protocol: Any, prose: str) -> None:
    """Assert arm-B prose matches arm-C's protocol methodology.

    Regenerates prose from the protocol object and compares — if they
    differ, the methodology content has drifted and the run must fail.
    """
    regenerated = protocol_to_prose(protocol)
    if regenerated != prose:
        raise RuntimeError(
            "PARITY GATE FAILED: arm-B prose does not match arm-C protocol.\n"
            "The methodology content has drifted between the prose generator\n"
            "and the protocol object used for enforcement.  Fix the prose\n"
            "generator or the protocol before running EXP1."
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="EXP1: Enforcement ablation runner")
    parser.add_argument("--smoke", action="store_true", help="Run 1 task with mocks")
    parser.add_argument("--force", action="store_true", help="Re-run completed trials")
    parser.add_argument("--limit", type=int, default=None,
                        help="Max number of tasks to process")
    parser.add_argument("--instance", type=str, default=None, dest="instance_id",
                        help="Run only a single instance by instance_id")
    parser.add_argument("--set", action="append", dest="overrides", default=[],
                        help="Override config key=value")
    args = parser.parse_args()

    cli_overrides = args.overrides if args.overrides else None
    config = load_config(cli_overrides=cli_overrides)

    rows = run_exp1(
        config=config,
        force=args.force,
        smoke=args.smoke,
        mock=args.smoke,
        limit=args.limit,
        instance_id=args.instance_id,
    )

    summary = _summarize(rows)
    print(f"\nEXP1 complete — {len(rows)} result rows")
    for line in summary:
        print(line)


def _summarize(rows: List[dict]) -> List[str]:
    """Build a short summary of results."""
    from collections import Counter

    lines: List[str] = []
    total = len(rows)
    resolved = sum(1 for r in rows if r.get("resolved"))
    excluded = sum(1 for r in rows if r.get("exclusion_reason"))

    lines.append(f"Total rows: {total}")
    lines.append(f"Resolved:   {resolved}")
    lines.append(f"Excluded:   {excluded}")

    by_arm: Dict[str, Counter] = {}
    for r in rows:
        arm = r.get("arm", "?")
        if arm not in by_arm:
            by_arm[arm] = Counter()
        by_arm[arm]["total"] += 1
        if r.get("resolved"):
            by_arm[arm]["resolved"] += 1

    for arm in sorted(by_arm):
        c = by_arm[arm]
        lines.append(f"  Arm {arm}: {c['resolved']}/{c['total']} resolved")

    return lines


if __name__ == "__main__":
    main()

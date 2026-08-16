"""Multi-instance batch scoring probe — reproduces the all-zero regression.

Single-instance scoring works (proven). The all-zero regression only appears
with the per-arm batch: MANY instances, each with a DISTINCT model_name, in
ONE predictions file. This probe reproduces that exactly with GOLD patches
(which MUST resolve), keeps swebench's logs, then replays scoring.py's exact
walk+map so we can see precisely where a correct report fails to attach.

Usage:
    python -m experiments.probe_score            # 3 instances from selection
    python -m experiments.probe_score N          # first N instances
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

DATASET = "princeton-nlp/SWE-bench_Verified"
PROBE_DIR = Path("experiments/results/exp1/probe").resolve()
SELECTION = Path("experiments/tasks/selection.jsonl")


def _gold_map(instance_ids):
    from datasets import load_dataset
    ds = load_dataset(DATASET, split="test")
    want = set(instance_ids)
    out = {}
    for row in ds:
        if row["instance_id"] in want:
            out[row["instance_id"]] = row["patch"]
    return out


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    sel = [json.loads(line) for line in SELECTION.read_text().splitlines() if line.strip()]
    instance_ids = [s["instance_id"] for s in sel][:n]
    print("instances:", instance_ids)
    golds = _gold_map(instance_ids)

    # Build the SAME predictions file the per-arm batch builds: distinct
    # model_name per instance.
    PROBE_DIR.mkdir(parents=True, exist_ok=True)
    preds = PROBE_DIR / "preds.jsonl"
    safe_map = {}
    lines = []
    for iid in instance_ids:
        model_name = f"exp1-a-{iid}-t1"
        safe = model_name.replace("/", "__")
        safe_map[(iid, model_name)] = safe
        lines.append(json.dumps({
            "instance_id": iid,
            "model_name_or_path": safe,
            "model_patch": golds[iid],
        }))
    preds.write_text("\n".join(lines) + "\n")

    run_id = "probe"
    cmd = [
        sys.executable, "-m", "swebench.harness.run_evaluation",
        "--dataset_name", DATASET,
        "--predictions_path", str(preds),
        "--instance_ids", ",".join(instance_ids),
        "--max_workers", "3",
        "--run_id", run_id,
        "--namespace", "swebench",
        "--cache_level", "instance",
    ]
    print("\n=== raw swebench, multi-instance, logs kept ===")
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(PROBE_DIR), timeout=1800)
    print("rc =", proc.returncode)
    print("stdout tail:", (proc.stdout or "")[-500:])

    # Replay scoring.py's EXACT walk.
    log_root = PROBE_DIR / "logs" / "run_evaluation" / run_id
    print("\n=== walk from log_root:", log_root, "===")
    safe_results = {}
    if log_root.exists():
        for report in log_root.rglob("report.json"):
            parts = report.relative_to(log_root).parts
            print("  report parts:", parts)
            if len(parts) >= 2:
                safe_model, iid = parts[0], parts[1]
                data = json.loads(report.read_text())
                rec = data.get(iid, {})
                safe_results[(iid, safe_model)] = bool(rec.get("resolved", False))
    print("\nsafe_results keys:", list(safe_results.keys()))

    print("\n=== attach: does each (iid, safe) find its report? ===")
    for iid in instance_ids:
        model_name = f"exp1-a-{iid}-t1"
        safe = safe_map[(iid, model_name)]
        key = (iid, safe)
        hit = safe_results.get(key, "MISSING")
        print(f"  {iid}: lookup {key} -> {hit}")


if __name__ == "__main__":
    main()

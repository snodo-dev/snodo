"""Re-score EXP1/EXP2 results from SAVED patches — no agent re-runs.

The agent dispatch (the expensive part) writes the full patch text into
results.jsonl. If a run scored in a broken env (e.g. uv re-synced numpy and
left it importing-broken), the patches are still good — we just re-run the
swebench scoring here, in a healthy env, and rewrite the resolved flags.

Run with the venv python directly (NOT `uv run`, which re-syncs and can
re-break the env):

    .venv/bin/python -m experiments.rescore_exp1 [in.jsonl] [out.jsonl]

Defaults: in = experiments/results/exp1/results.jsonl
          out = experiments/results/exp1/results.rescored.jsonl
"""

from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

from experiments.scoring import score_predictions_batch

ARMS = ("a", "b", "c", "bprime")
HUGE = 200_000  # skip pathological patches (e.g. a committed venv) — score them False
CHUNK = 20      # score in chunks so one stuck instance can't fail a 100-wide batch


def main() -> None:
    inp = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("experiments/results/exp1/results.jsonl")
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("experiments/results/exp1/results.rescored.jsonl")

    rows = [json.loads(l) for l in inp.read_text().splitlines() if l.strip()]
    print(f"loaded {len(rows)} rows from {inp}")

    # Re-score arm rows per arm (distinct instances within an arm -> one batch).
    for arm in ARMS:
        arm_rows = [r for r in rows if r.get("arm") == arm]
        batch = []
        skipped = 0
        for r in arm_rows:
            patch = r.get("patch") or ""
            if not patch or len(patch) > HUGE:
                skipped += 1
                continue
            mn = f"{arm}-{r['instance_id']}"
            batch.append(({"instance_id": r["instance_id"]}, patch, mn))
        if not batch:
            print(f"arm {arm}: nothing to score ({skipped} skipped)")
            continue
        print(f"arm {arm}: scoring {len(batch)} patches ({skipped} empty/huge skipped)...")
        # Chunk the batch: a single 100-instance harness call can exceed the
        # per-invocation timeout, and a stuck instance would fail the whole
        # batch. Small chunks fail-isolate and stay well under the timeout.
        res: dict = {}
        for i in range(0, len(batch), CHUNK):
            chunk = batch[i:i + CHUNK]
            print(f"  chunk {i // CHUNK + 1}: {len(chunk)} patches...", flush=True)
            res.update(score_predictions_batch(chunk, max_workers=4))
        for r in arm_rows:
            mn = f"{arm}-{r['instance_id']}"
            v = res.get((r["instance_id"], mn))
            if v is not None:
                r["resolved"] = bool(v.get("resolved", False))
                r["n_fail_to_pass_passed"] = v.get("n_fail_to_pass_passed", 0)
                r["regressions"] = v.get("regressions", 0)
                r["error"] = v.get("error")

    out.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    print(f"\nwrote {out}")

    # Summary
    by = collections.defaultdict(lambda: [0, 0])
    for r in rows:
        a = r.get("arm")
        if a in ARMS:
            by[a][1] += 1
            by[a][0] += int(bool(r.get("resolved")))
    print("--- re-scored resolve rates ---")
    for a in ARMS:
        k, n = by[a]
        print(f"  arm {a}: {k}/{n} = {k/n:.1%}" if n else f"  arm {a}: 0/0")


if __name__ == "__main__":
    main()

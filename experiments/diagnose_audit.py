#!/usr/bin/env python3
"""Read a specific task's arm-C audit log."""
import json
import sys
from collections import defaultdict
from pathlib import Path

ROWS = [json.loads(l) for l in open(Path(__file__).parent / "results" / "exp1" / "results.jsonl") if l.strip()]

instance_id = sys.argv[1] if len(sys.argv) > 1 else None

if instance_id is None:
    res = defaultdict(lambda: {})
    for r in ROWS:
        if r.get("arm") in ("a", "b", "c"):
            res[r["instance_id"]][r["arm"]] = res[r["instance_id"]].get(r["arm"], False) or r.get("resolved", False)
    print("Usage: python diagnose_audit.py <instance_id>")
    print("\nDivergent instances:")
    for iid in sorted(res):
        d = res[iid]
        if len(set(d.values())) > 1:
            print(f"  {iid}  a={int(d.get('a',0))} b={int(d.get('b',0))} c={int(d.get('c',0))}")
    sys.exit(0)

c_rows = [r for r in ROWS if r.get("arm") == "c" and r["instance_id"] == instance_id]
if not c_rows:
    print(f"No arm C rows for {instance_id}")
    sys.exit(1)

for r in c_rows:
    run_id = r.get("run_id", "?")
    resolved = r.get("resolved", False)
    error = r.get("error", "")
    closure = r.get("closure_json") or {}
    trial = r.get("trial_id", "?")

    print(f"\n=== trial={trial} resolved={resolved} ===")
    if error:
        print(f"ERROR: {error}")
    print(f"Closure: outcome={closure.get('outcome')} attempts={closure.get('attempts_used')}")

    audit_path = Path(f"experiments/results/exp1/runs/{run_id}/arm-c-audit.log")
    if audit_path.exists():
        print(f"\n--- Audit log ({audit_path}) ---")
        print(audit_path.read_text()[-8000:])
    else:
        print(f"Audit log not found: {audit_path}")

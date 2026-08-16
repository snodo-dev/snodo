#!/bin/bash
# Run on GPU10 to diagnose EXP1 arm-C underperformance
# Usage: bash experiments/run_diagnostics.sh

set -e
cd "$(dirname "$0")/.."  # cd to repo root

echo "=== Step 1: Built-in analysis ==="
python -m experiments.analyze_exp1 experiments/results/exp1/results.jsonl

echo ""
echo "=== Step 2: Deep-dive diagnostic ==="
python experiments/diagnose_exp1.py

echo ""
echo "=== Step 3: Audit log for first divergent task ==="
# Find a task where C fails but A/B succeed
DIVERGENT=$(python3 -c "
import json
from collections import defaultdict
rows=[json.loads(l) for l in open('experiments/results/exp1/results.jsonl') if l.strip()]
res=defaultdict(lambda:{})
for r in rows:
  a=r.get('arm','')
  if a in ('a','b','c'):
    res[r['instance_id']][a]=res[r['instance_id']].get(a,False) or r.get('resolved',False)
for t,d in sorted(res.items()):
  if not d.get('c') and (d.get('a') or d.get('b')):
    print(t); break
")
if [ -n "$DIVERGENT" ]; then
    echo "Inspecting: $DIVERGENT"
    python experiments/diagnose_audit.py "$DIVERGENT"
else
    echo "No C-worse-than-A/B task found"
fi

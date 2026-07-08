"""Analyze EXP1 results: paired arm comparison + enforcement health.

The hypothesis is "enforcement (arm c) changes outcomes vs instruction (b) /
bare (a)". Because arms are run on the SAME tasks, the correct test is a
PAIRED one (McNemar), not a comparison of marginal resolve rates. This script:

  1. Overview: resolve rate per arm (+ Wilson CI), exclusions, errors.
  2. Paired McNemar (exact) for a-vs-c, b-vs-c, a-vs-b: discordant pairs + p.
  3. Per-repo resolve rate per arm.
  4. Divergence table: tasks where the arms disagree.
  5. Enforcement health (arm c): closure outcomes, attempts, spec-authoring.

Usage:
    python -m experiments.analyze_exp1 [results.jsonl]
"""

from __future__ import annotations

import collections
import json
import math
import sys
from pathlib import Path

ARMS = ("a", "b", "c")
DEFAULT = Path("experiments/results/exp1/results.jsonl")


def wilson(k: int, n: int) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    z = 1.96
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0, center - half), min(1, center + half))


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact McNemar p-value from discordant counts b, c."""
    n = b + c
    if n == 0:
        return 1.0
    from math import comb
    k = min(b, c)
    tail = sum(comb(n, i) for i in range(0, k + 1)) / (2 ** n)
    return min(1.0, 2 * tail)


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT
    rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]

    # Collapse to one resolved bool per (instance, arm): resolved if ANY trial did.
    res = collections.defaultdict(dict)   # instance -> arm -> bool
    repo = {}
    for r in rows:
        a = r.get("arm")
        if a not in ARMS:
            continue
        iid = r["instance_id"]
        res[iid][a] = res[iid].get(a, False) or bool(r["resolved"])
        repo[iid] = r.get("repo") or (rows and "?")
    tasks = sorted(res)

    print(f"=== EXP1 analysis: {path.name} ===")
    print(f"tasks with all 3 arms: {sum(1 for t in tasks if len(res[t])==3)} / {len(tasks)}")
    excl = sum(1 for r in rows if r.get("exclusion_reason"))
    errs = collections.Counter(str(r.get("error"))[:30] for r in rows
                               if r.get("arm") in ARMS and r.get("error"))
    print(f"excluded rows: {excl} | error rows: {sum(errs.values())} {dict(errs)}\n")

    # 1. Marginal resolve rate per arm (+ Wilson CI)
    print("--- resolve rate per arm (Wilson 95% CI) ---")
    for a in ARMS:
        k = sum(1 for t in tasks if res[t].get(a))
        n = sum(1 for t in tasks if a in res[t])
        lo, hi = wilson(k, n)
        print(f"  arm {a}: {k:3d}/{n:<3d} = {k/n if n else 0:.1%}  [{lo:.1%}, {hi:.1%}]")

    # 2. Paired McNemar
    print("\n--- paired McNemar (exact, two-sided) ---")
    for x, y in (("a", "c"), ("b", "c"), ("a", "b")):
        both = [t for t in tasks if x in res[t] and y in res[t]]
        b = sum(1 for t in both if res[t][x] and not res[t][y])   # x only
        c = sum(1 for t in both if res[t][y] and not res[t][x])   # y only
        p = mcnemar_exact(b, c)
        print(f"  {x} vs {y}: {x}-only={b}  {y}-only={c}  concordant={len(both)-b-c}  p={p:.4f}")

    # 3. Per-repo
    print("\n--- resolve rate per repo (a / b / c) ---")
    byrepo = collections.defaultdict(lambda: {a: [0, 0] for a in ARMS})
    for t in tasks:
        for a in ARMS:
            if a in res[t]:
                byrepo[repo[t]][a][1] += 1
                byrepo[repo[t]][a][0] += int(res[t][a])
    for rp in sorted(byrepo):
        d = byrepo[rp]
        cells = "  ".join(f"{a}:{d[a][0]}/{d[a][1]}" for a in ARMS)
        print(f"  {rp:28} {cells}")

    # 4. Divergence
    print("\n--- divergence (arms disagree) ---")
    div = [t for t in tasks if len({res[t].get(a) for a in ARMS if a in res[t]}) > 1]
    print(f"  {len(div)} tasks differ across arms:")
    for t in div:
        print(f"    {t:34} a={int(res[t].get('a',0))} b={int(res[t].get('b',0))} c={int(res[t].get('c',0))}")

    # 5. Enforcement health (arm c)
    print("\n--- arm-c enforcement health ---")
    ch = collections.Counter()
    att = collections.Counter()
    for r in rows:
        if r.get("arm") == "c":
            cj = r.get("closure_json") or {}
            ch[cj.get("outcome")] += 1
            att[cj.get("attempts_used")] += 1
    print("  closure outcomes:", dict(ch))
    print("  attempts used:   ", dict(att))


if __name__ == "__main__":
    main()

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


_INFRA_MARKERS = (
    "timeout", "429", "rate limit", "rate_limit", "resource_exhausted",
    "utf-8", "codec can't decode", "no report",
    "provider not", "connection", "quota", "overloaded", "503", "502",
)
# NOTE: a clean-exit empty patch ("empty patch (rc=0)") is a GENUINE model miss,
# not infra — the agent ran fine and produced no fix. Do NOT mark it infra, or
# weak-model whiffs get excluded and inflate that arm (seen on gpt-oss:20b: 46
# bare / 25 prose empties wrongly dropped). Real transient empties still match
# timeout/429/connection above.


def _is_infra(err) -> bool:
    """True if an unresolved row failed for infra/transient reasons (not a real
    model miss) — these should be excluded from the denominator, not counted as
    failures, or rate-limit/timeout noise falsifies the resolve rate."""
    s = str(err or "").lower()
    return any(m in s for m in _INFRA_MARKERS)


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT
    rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]

    # Collapse to one resolved bool per (instance, arm): resolved if ANY trial did.
    res = collections.defaultdict(dict)   # instance -> arm -> bool
    infra = collections.defaultdict(dict)  # instance -> arm -> infra-failed bool
    repo = {}
    for r in rows:
        a = r.get("arm")
        if a not in ARMS:
            continue
        iid = r["instance_id"]
        res[iid][a] = res[iid].get(a, False) or bool(r["resolved"])
        # infra failure = unresolved AND error looks transient/infrastructure
        # (rate-limit/timeout/empty/decode/no-report), NOT a genuine model miss.
        # These must be EXCLUDED, not counted as failures, or they falsify rates.
        if not bool(r["resolved"]):
            infra[iid][a] = infra[iid].get(a, False) or _is_infra(r.get("error"))
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

    # 1b. Infra-failure-adjusted (clean) rates — exclude transient failures
    print("\n--- infra failures + clean rate (excludes rate-limit/timeout/empty/decode) ---")
    inf_by = {}
    for a in ARMS:
        n = sum(1 for t in tasks if a in res[t])
        nf = sum(1 for t in tasks if infra[t].get(a))
        k = sum(1 for t in tasks if res[t].get(a))
        cn = n - nf
        inf_by[a] = nf
        lo, hi = wilson(k, cn) if cn else (0.0, 0.0)
        print(f"  arm {a}: infra-fails={nf:3d}  clean {k}/{cn} = {k/cn if cn else 0:.1%}  [{lo:.1%}, {hi:.1%}]")
    if inf_by and max(inf_by.values()) - min(inf_by.values()) >= 5:
        print(f"  ** WARNING: infra-fails arm-SKEWED {inf_by} — biases the comparison; retry/exclude those rows before trusting it.")

    # 2. Paired McNemar — raw, and "clean" (drop tasks where EITHER arm infra-failed)
    print("\n--- paired McNemar (exact, two-sided) ---")
    for x, y in (("a", "c"), ("b", "c"), ("a", "b")):
        both = [t for t in tasks if x in res[t] and y in res[t]]
        b = sum(1 for t in both if res[t][x] and not res[t][y])
        c = sum(1 for t in both if res[t][y] and not res[t][x])
        p = mcnemar_exact(b, c)
        clean = [t for t in both if not infra[t].get(x) and not infra[t].get(y)]
        cb = sum(1 for t in clean if res[t][x] and not res[t][y])
        cc = sum(1 for t in clean if res[t][y] and not res[t][x])
        cp = mcnemar_exact(cb, cc)
        print(f"  {x} vs {y}: {x}-only={b} {y}-only={c} concordant={len(both)-b-c} p={p:.4f}"
              f"   | clean(n={len(clean)}): {x}-only={cb} {y}-only={cc} p={cp:.4f}")

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

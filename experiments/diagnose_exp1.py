#!/usr/bin/env python3
"""EXP1 diagnostic: deep-dive into arm-C underperformance."""
import json
from collections import Counter, defaultdict
from pathlib import Path

ROWS = [json.loads(line) for line in open(Path(__file__).parent / "results" / "exp1" / "results.jsonl") if line.strip()]
ARMS = ("a", "b", "c")

# ---- 1. Error breakdown per arm ----
print("=" * 70)
print("1. ERROR BREAKDOWN BY ARM")
print("=" * 70)
for arm in ARMS:
    arm_rows = [r for r in ROWS if r.get("arm") == arm]
    err_count = sum(1 for r in arm_rows if r.get("error"))
    print(f"\nArm {arm}: {err_count}/{len(arm_rows)} errors ({err_count/len(arm_rows)*100:.1f}%)")
    errs = Counter(r.get("error", "")[:120] for r in arm_rows if r.get("error"))
    for e, n in errs.most_common(15):
        print(f"  {n:3d} | {e}")

# ---- 2. Arm C closure outcomes ----
print("\n" + "=" * 70)
print("2. ARM C CLOSURE OUTCOMES")
print("=" * 70)
c_rows = [r for r in ROWS if r.get("arm") == "c"]
cj = Counter()
cj_resolved = defaultdict(lambda: [0, 0])
for r in c_rows:
    d = r.get("closure_json") or {}
    out = d.get("outcome", "no_closure")
    cj[out] += 1
    cj_resolved[out][1] += 1
    if r.get("resolved"):
        cj_resolved[out][0] += 1
print(f"{'outcome':30s} {'count':>5s}  {'resolved':>8s}  {'rate':>6s}")
print("-" * 55)
for k, v in cj.most_common():
    rr, tt = cj_resolved[k]
    rate = f"{rr/tt*100:.0f}%" if tt else "N/A"
    print(f"  {k:28s} {v:5d}  {rr:3d}/{tt:<3d}  {rate:>6s}")

# ---- 3. Attempts used distribution ----
print("\n" + "=" * 70)
print("3. ARM C ATTEMPTS USED")
print("=" * 70)
att_resolved = defaultdict(lambda: [0, 0])
for r in c_rows:
    d = r.get("closure_json") or {}
    n = d.get("attempts_used", None)
    if n is not None:
        att_resolved[n][1] += 1
        if r.get("resolved"):
            att_resolved[n][0] += 1
print(f"{'attempts':>9s} {'count':>5s}  {'resolved':>8s}  {'rate':>6s}")
print("-" * 35)
for n in sorted(att_resolved):
    rr, tt = att_resolved[n]
    rate = f"{rr/tt*100:.0f}%" if tt else "N/A"
    print(f"  {n:6d}  {tt:5d}  {rr:3d}/{tt:<3d}  {rate:>6s}")

# ---- 4. Patch health: empty patches and mean sizes ----
print("\n" + "=" * 70)
print("4. PATCH HEALTH BY ARM (non-error rows)")
print("=" * 70)
for arm in ARMS:
    good = [(r.get("resolved", False), len(r.get("patch") or ""))
            for r in ROWS if r.get("arm") == arm and not r.get("error")]
    resolved_patches = [plen for res, plen in good if res]
    unresolved_patches = [plen for res, plen in good if not res]
    empty_res = sum(1 for plen in resolved_patches if plen == 0)
    empty_ures = sum(1 for plen in unresolved_patches if plen == 0)
    mean_res = sum(resolved_patches) / max(1, len(resolved_patches))
    mean_ures = sum(unresolved_patches) / max(1, len(unresolved_patches))
    print(f"  Arm {arm}:")
    print(f"    Resolved:   mean_len={mean_res:7.0f}  empty={empty_res}/{len(resolved_patches)}")
    print(f"    Unresolved: mean_len={mean_ures:7.0f}  empty={empty_ures}/{len(unresolved_patches)}")

# ---- 5. Wall time by arm ----
print("\n" + "=" * 70)
print("5. WALL TIME BY ARM (non-error rows)")
print("=" * 70)
for arm in ARMS:
    times = [r["wall_s"] for r in ROWS if r["arm"] == arm and not r.get("error") and r.get("wall_s", 0) > 0]
    if times:
        sorted_t = sorted(times)
        p50 = sorted_t[len(sorted_t)//2]
        p90 = sorted_t[int(len(sorted_t)*0.9)]
        print(f"  Arm {arm}: mean={sum(times)/len(times):.0f}s  median={p50:.0f}s  p90={p90:.0f}s  max={max(times):.0f}s")

# ---- 6. Divergence: tasks where C fails but A or B succeeds ----
print("\n" + "=" * 70)
print("6. TASKS WHERE C FAILS, A OR B SUCCEEDS")
print("=" * 70)
res = defaultdict(lambda: {})
for r in ROWS:
    a = r.get("arm")
    if a in ARMS:
        iid = r["instance_id"]
        res[iid][a] = res[iid].get(a, False) or r.get("resolved", False)
bad = [(t, res[t]) for t in sorted(res) if not res[t].get("c") and (res[t].get("a") or res[t].get("b"))]
print(f"  Count: {len(bad)}")
for t, d in bad:
    print(f"  {t:42s} a={int(d.get('a',0))} b={int(d.get('b',0))} c={int(d.get('c',0))}")

# ---- 7. The reverse: tasks where C succeeds but A/B fail ----
print("\n" + "=" * 70)
print("7. TASKS WHERE C SUCCEEDS, A OR B FAILS")
print("=" * 70)
good = [(t, res[t]) for t in sorted(res) if res[t].get("c") and not (res[t].get("a") and res[t].get("b"))]
print(f"  Count: {len(good)}")
for t, d in good:
    print(f"  {t:42s} a={int(d.get('a',0))} b={int(d.get('b',0))} c={int(d.get('c',0))}")

# ---- 8. Per-task detail for divergent instances (patch content) ----
print("\n" + "=" * 70)
print("8. DIVERGENT TASK DETAILS (patch lengths, errors)")
print("=" * 70)
task_rows = defaultdict(lambda: {})
for r in ROWS:
    if r.get("arm") in ARMS:
        task_rows[r["instance_id"]][r["arm"]] = r
for t in sorted(task_rows):
    d = task_rows[t]
    a_ok = d.get("a", {}).get("resolved", False)
    b_ok = d.get("b", {}).get("resolved", False)
    c_ok = d.get("c", {}).get("resolved", False)
    if a_ok == b_ok == c_ok:
        continue
    print(f"\n--- {t} ---")
    print(f"  resolved:  a={a_ok}  b={b_ok}  c={c_ok}")
    for arm_label, r in [("a", d.get("a")), ("b", d.get("b")), ("c", d.get("c"))]:
        if r:
            err = r.get("error", "")
            patch_len = len(r.get("patch") or "")
            wall = r.get("wall_s", 0)
            attempts = (r.get("closure_json") or {}).get("attempts_used", "-") if arm_label == "c" else "-"
            outcome = (r.get("closure_json") or {}).get("outcome", "-") if arm_label == "c" else "-"
            print(f"  arm {arm_label}: patch_len={patch_len:5d}  wall={wall:.0f}s  err={'YES' if err else '-'}")
            if arm_label == "c":
                print(f"         attempts={attempts}  outcome={outcome}")

print("\nDone.")

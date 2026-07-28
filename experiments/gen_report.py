#!/usr/bin/env python3
"""Generate temp.md with complete EXP1 diagnostic analysis."""
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

OUT = Path(__file__).parent / "temp.md"
RESULTS = Path(__file__).parent / "results" / "exp1" / "results.jsonl"
ROWS = [json.loads(l) for l in open(RESULTS) if l.strip()]
ARMS = ("a", "b", "c")

lines = []
def w(s=""):
    lines.append(s)

# Compute per-task resolve (any trial resolves = resolved)
res = defaultdict(lambda: {})
for r in ROWS:
    a = r.get("arm")
    if a in ARMS:
        iid = r["instance_id"]
        res[iid][a] = res[iid].get(a, False) or r.get("resolved", False)
tasks = sorted(res)

c_rows = [r for r in ROWS if r.get("arm") == "c"]

# ================================================================================
w("# EXP1 Arm-C Underperformance — Full Diagnostic")
w()

# ================================================================================
w("## 1. Summary")
w()
w("| Arm | Rows | Resolved | Rate |")
w("|-----|------|----------|------|")
for arm in ARMS:
    arm_rows = [r for r in ROWS if r.get("arm") == arm]
    n = len(arm_rows)
    ok = sum(1 for r in arm_rows if r.get("resolved"))
    w(f"| {arm} | {n} | {ok} | {ok/n*100:.1f}% |")
w()

# ================================================================================
w("## 2. A-resolved, C-not-resolved (10 tasks)")
w()
both = [(t, res[t]) for t in tasks if res[t].get("a") and not res[t].get("c")]
w(f"**Count: {len(both)}**")
w()
for t, d in sorted(both):
    w(f"### {t}")
    w(f"a={int(d.get('a',0))} b={int(d.get('b',0))} c={int(d.get('c',0))}")
    for arm_label in ARMS:
        for r in ROWS:
            if r.get("instance_id") == t and r.get("arm") == arm_label:
                patch = r.get("patch") or ""
                err = r.get("error") or "-"
                w(f"- **arm {arm_label}**: patch_len={len(patch):5d} wall={r.get('wall_s',0):.0f}s resolved={r.get('resolved',False)} error={err[:100]}")
                if arm_label == "c":
                    cj = r.get("closure_json") or {}
                    w(f"  - closure: outcome={cj.get('outcome','?')} attempts={cj.get('attempts_used','?')}")
                break
    w()

# ================================================================================
w("## 3. C-resolved, A-not-resolved (9 tasks)")
w()
both2 = [(t, res[t]) for t in tasks if res[t].get("c") and not res[t].get("a")]
w(f"**Count: {len(both2)}**")
w()
for t, d in sorted(both2):
    w(f"### {t}")
    w(f"a={int(d.get('a',0))} b={int(d.get('b',0))} c={int(d.get('c',0))}")
    for arm_label in ARMS:
        for r in ROWS:
            if r.get("instance_id") == t and r.get("arm") == arm_label:
                patch = r.get("patch") or ""
                err = r.get("error") or "-"
                w(f"- **arm {arm_label}**: patch_len={len(patch):5d} wall={r.get('wall_s',0):.0f}s resolved={r.get('resolved',False)} error={err[:100]}")
                if arm_label == "c":
                    cj = r.get("closure_json") or {}
                    w(f"  - closure: outcome={cj.get('outcome','?')} attempts={cj.get('attempts_used','?')}")
                break
    w()

# ================================================================================
w("## 4. Classification of the 9 'C wins'")
w()
for t, d in sorted(both2):
    a_ok = d.get("a", False)
    b_ok = d.get("b", False)
    w(f"### {t} (a={int(a_ok)} b={int(b_ok)} c=1)")

    # Get details for A and B
    a_detail = b_detail = ""
    for r in ROWS:
        if r.get("instance_id") == t and r.get("arm") == "a":
            a_detail = f"error={r.get('error','-')[:80]} patch_len={len(r.get('patch') or '')} wall={r.get('wall_s',0):.0f}s"
        if r.get("instance_id") == t and r.get("arm") == "b":
            b_detail = f"error={r.get('error','-')[:80]} patch_len={len(r.get('patch') or '')} wall={r.get('wall_s',0):.0f}s"

    if not a_ok and not b_ok:
        w(f"- **GENUINE sole C win** — neither A nor B resolved")
        w(f"  - A: {a_detail}")
        w(f"  - B: {b_detail}")
    elif a_ok and not b_ok:
        # B had issue
        b_rows = [r for r in ROWS if r.get("instance_id") == t and r.get("arm") == "b"]
        b_err = (b_rows[0].get("error") or "") if b_rows else ""
        b_patch_len = len(b_rows[0].get("patch") or "") if b_rows else 0
        if "timeout" in b_err.lower():
            w(f"- **SHARED win (A also won)** — B timed out at {b_rows[0].get('wall_s',0):.0f}s")
        elif b_patch_len == 0:
            w(f"- **SHARED win (A also won)** — B produced empty patch")
        elif b_patch_len > 50000:
            w(f"- **SHARED win (A also won)** — B hallucinated ({b_patch_len} char patch)")
        else:
            w(f"- **SHARED win (A also won)** — B: {a_detail}")
    elif not a_ok and b_ok:
        w(f"- **SHARED win (B also won)** — A: {a_detail}")
    else:
        w(f"- **SHARED win (both A and B won too)**")
    w()

# ================================================================================
w("## 5. Classification of the 17 'C losses'")
w()
for t, d in sorted(both):
    a_ok = d.get("a", False)
    b_ok = d.get("b", False)
    cj = {}
    patch = ""
    c_error = ""
    c_wall = 0
    for r in ROWS:
        if r.get("instance_id") == t and r.get("arm") == "c":
            cj = r.get("closure_json") or {}
            patch = r.get("patch") or ""
            c_error = r.get("error") or ""
            c_wall = r.get("wall_s", 0)
            break

    a_patch_len = b_patch_len = 0
    for r in ROWS:
        if r.get("instance_id") == t and r.get("arm") == "a":
            a_patch_len = len(r.get("patch") or "")
        if r.get("instance_id") == t and r.get("arm") == "b":
            b_patch_len = len(r.get("patch") or "")

    w(f"### {t} (a={int(a_ok)} b={int(b_ok)} c=0)")
    w(f"- closure: outcome={cj.get('outcome','?')} attempts={cj.get('attempts_used','?')}")
    w(f"- c error: {'YES: ' + c_error[:100] if c_error else 'none'}")
    w(f"- c wall: {c_wall:.0f}s")
    w(f"- c patch_len: {len(patch)}")
    w(f"- a patch_len: {a_patch_len}")
    w(f"- b patch_len: {b_patch_len}")

    classification = "UNKNOWN"
    if len(patch) == 0:
        classification = "BUG: empty patch"
    elif c_error and ("graph build failed" in c_error or "closure failed" in c_error):
        classification = "BUG: engine error"
    elif c_error and "timeout" in c_error.lower():
        classification = "BUG: timeout"
    else:
        classification = "PROMPT DIFF: OpenCodeAdapter produced wrong answer"

    w(f"- **classification: {classification}**")
    w()

# ================================================================================
w("## 6. Net effect summary")
w()
a_only = sum(1 for t in tasks if res[t].get("a") and not res[t].get("c"))
c_only = sum(1 for t in tasks if not res[t].get("a") and res[t].get("c"))
b_only = sum(1 for t in tasks if res[t].get("b") and not res[t].get("c"))
w(f"| Metric | Count |")
w(f"|--------|-------|")
w(f"| Tasks where A resolves, C does NOT | {a_only} |")
w(f"| Tasks where C resolves, A does NOT | {c_only} |")
w(f"| Tasks where B resolves, C does NOT | {b_only} |")
w(f"| Net C vs A | +{c_only} -{a_only} = **{c_only - a_only}** |")
w()

# ================================================================================
w("## 7. Arm C closure outcomes")
w()
cj = Counter()
for r in c_rows:
    d = r.get("closure_json") or {}
    out = d.get("outcome", "no_closure")
    cj[out] += 1
w(f"| outcome | count |")
w(f"|---------|-------|")
for k, v in cj.most_common():
    w(f"| {k} | {v} |")
w()

# ================================================================================
w("## 8. Task concordance matrix")
w()
w(f"**Tasks where all 3 agree: {sum(1 for t in tasks if len(set(res[t].values())) == 1)}**")
w(f"**Tasks with split decision: {sum(1 for t in tasks if len(set(res[t].values())) > 1)}**")
w()
# Count the 8 patterns
patterns = Counter()
for t in tasks:
    d = res[t]
    pat = f"a={int(d.get('a',0))} b={int(d.get('b',0))} c={int(d.get('c',0))}"
    patterns[pat] += 1
w("| Pattern | Count |")
w("|---------|-------|")
for p, n in patterns.most_common():
    w(f"| {p} | {n} |")
w()

# ================================================================================
w("## 9. PATCH DIFFS — 10 tasks where A resolved and C did not")
w()
for t, d in sorted(both):
    w(f"### {t}")
    for arm_label in ("a", "c"):
        for r in ROWS:
            if r.get("instance_id") == t and r.get("arm") == arm_label:
                patch = (r.get("patch") or "")
                w(f"**Arm {arm_label}** (len={len(patch)}, resolved={r.get('resolved',False)}):")
                w()
                w("```diff")
                w(patch[:2000])
                if len(patch) > 2000:
                    w(f"... ({len(patch) - 2000} more chars)")
                w("```")
                w()
                break
    w("---")
    w()

OUT.write_text("\n".join(lines))
print(f"Wrote {OUT} ({len(lines)} lines)")

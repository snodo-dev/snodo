# EXP2 — Tailored Bug-Fix Protocol: Findings (preliminary, single run)

**Status:** one run, re-scored from saved patches · **n:** 94 (100 tasks, 6 gold-excluded)
**Coder:** `deepseek/deepseek-v4-flash` · **Protocol (arm c):** `bugfix-surgeon`
**Scorer:** SWE-bench Verified harness · **date:** 2026-07-27

## Headline

EXP1 showed a *generic* protocol (spec-authoring) **hurt** bug fixes — enforcement
was the worst arm (50.5%), significantly below prose. We diagnosed the mechanism
(over-elaboration: bigger patches resolve less), built a *tailored* minimality
protocol (`bugfix-surgeon`: no spec-authoring, post-execute reviewer rejects sprawl,
K-recovery re-tightens), fixed the wiring so enforcement actually engages, and
re-ran. The sign flipped and the effect is now statistically significant.

**Same minimality methodology, two delivery modes:** as *instruction* (arm b, 55.3%)
it does nothing (b ≈ a); as *enforcement* (arm c, 69.1%) it wins — paired McNemar
16 discordant to 3, **p = 0.0044**, which survives Bonferroni correction. That ~14pt
gap is pure enforcement (identical methodology content, guaranteed by the parity
gate). This is the "enforcement > instruction" thesis demonstrated — conditional on
the protocol being tailored to the task.

## Results (within-run, paired)

| arm | resolve | rate | 95% CI |
|-----|---------|------|--------|
| A — bare                    | 56/94 | 59.6% | 49.5–68.9% |
| B — prose (minimality told) | 52/94 | 55.3% | 45.3–65.0% |
| C — surgeon (minimality enforced) | 65/94 | **69.1%** | 59.2–77.6% |

Paired McNemar (exact, two-sided):

| comparison | X-only / Y-only | p |
|------------|-----------------|---|
| **prose vs enforced (B/C)** | 3 / 16 | **0.0044** (sig., Bonferroni-robust) |
| bare vs enforced (A/C)      | 5 / 14 | 0.064 (near-sig. trend) |
| bare vs prose (A/B)         | 7 / 3  | 0.34 (ns) |

Enforcement genuinely engaged: closures 75 resolved / 19 recovery-exhausted;
attempts used 1:49, 2:13, 3:11, 4:21 — i.e. 45 of 94 tasks needed 2–4 recovery
rounds, and the loop re-tightened them. arm c uniquely solved many (matplotlib ×4,
django ×3, sphinx, pytest, sympy, pylint) with few unique losses.

## Contrast with EXP1

| arm | EXP1 (generic/spec-authoring) | EXP2 (tailored/minimality) |
|-----|-------------------------------|----------------------------|
| a bare | 55.6% | 59.6% |
| b prose | 60.6% | 55.3% |
| c enforced | 50.5% (worst) | 69.1% (best) |

Cross-run arm comparisons are **not** valid (b's prose is protocol-derived and
differs between runs; denominators/scoring differ). Read each run within itself. The
invariant control — arm a, which gets no methodology — is stable across runs (55.6 →
59.6, overlapping CIs), which validates the setup. The story is: **the wrong protocol
hurt (EXP1), a task-tailored one helped (EXP2).**

## Data-integrity notes (why re-scoring was needed)

The run scored all-zero initially due to `uv run` re-syncing and corrupting numpy's
C-extensions at scoring time. Because full patch text is saved in results.jsonl, we
re-scored from disk in a repaired env (no agent re-runs). A second bug surfaced:
`extract_patch` did `.strip()`, removing the trailing newline every diff needs, so
`patch` rejected them ("unexpectedly ends in middle of line"). Appending a newline
recovered 20 of 45 such rows (verified: pylint-6903 as-is False → +newline True).
`extract_patch` is now fixed to preserve exactly one trailing newline.

## Limitations (why this is preliminary, not a claim yet)

- **Single run, single model, single task set.** Needs replication + a different 100
  + a different model (see validation roadmap / task list).
- **25 patches still fail to apply** (unrecovered malformations / genuine failures) —
  counted as failures, so the rates are *conservative*; distribution across arms not
  yet audited.
- **a-vs-c is only a trend** (p = 0.064) — underpowered at n=94; n≈200–300 should
  settle it.
- **Contaminated benchmark.** SWE-bench Verified is pre-2024 and almost certainly in
  training data; the effect should be reconfirmed on uncontaminated tasks.
- **Enforcement here = spec-free minimality gate + recovery**, not snodo's full
  validator suite.

## Replication — seed-29, independent sample (n=96)

Re-ran the identical protocol/model on a DIFFERENT stratified 100-task sample
(seed 29). Scored cleanly inline (`.venv/bin/python`, no `uv run`; extract_patch
newline fix in place → **zero** malformed-patch artifacts). Result held:

| arm | resolve | rate |
|-----|---------|------|
| A — bare    | 47/96 | 49.0% |
| B — prose   | 51/96 | 53.1% |
| C — surgeon | 64/96 | **66.7%** |

Paired McNemar: **a-vs-c 2/19 p=0.0002**, **b-vs-c 6/19 p=0.0146**, a-vs-b 2/6 p=0.29.

**Combined across the two independent samples:**

| comparison | seed-13 | seed-29 |
|------------|---------|---------|
| enforced vs bare (A–C)  | p=0.064 | p=0.0002 |
| enforced vs prose (B–C) | p=0.004 | p=0.015  |
| bare vs prose (A–B)     | 0.34    | 0.29     |

Enforcement beats instruction significantly on **both** samples (Bonferroni-robust);
enforcement beats bare significantly on seed-29 and trended on seed-13; instruction
alone never beat bare. The effect replicates. Enforcement health seed-29: 69 resolved
/ 24 recovery-exhausted closures, 44 tasks used 2-4 recovery rounds. arm c also had
the fewest genuine failures (32 vs a 49 / b 45).

**Standing conclusion (bug fixes, deepseek, SWE-bench Verified):** a task-tailored
enforcement protocol reliably and significantly improves resolution over the same
methodology delivered as instruction, replicated across two independent samples.
Still open before a broad claim: model transfer, an uncontaminated task set, and the
model-commodity 2x2.

## Powered run — seed-47, n=191 (v4-flash)

High-power confirmation on a third flash sample (seed-47, 200 tasks, 191 scored):

| arm | resolve | rate | 95% CI |
|-----|---------|------|--------|
| A — bare    | 96/191 | 50.3% | 43.2–57.3% |
| B — prose   | 96/191 | 50.3% | 43.2–57.3% |
| C — surgeon | 128/191 | **67.0%** | 60.1–73.3% |

Paired McNemar: **a-vs-c 8/40 p<0.0001**, **b-vs-c 7/39 p<0.0001**, a-vs-b 11/11 p=1.0.
Enforcement engaged: 146 resolved / 45 recovery-exhausted closures; 84 tasks used 2-4
recovery rounds. Arm c uniquely solved a large block (django ×15, matplotlib ×13, all
a=0 b=0 c=1).

Scorer-bug note (fixed): the run first mis-scored arm-c to 0/191 because
`score_predictions_batch`'s timeout handler returned an N×N cross-product of failures
when the 190-patch batch exceeded the 2400s limit (only this run was large enough to
trip it). Fixed the cross-product; recovered by chunked re-scoring. The dispatch was
always intact.

### Full evidence base (enforcement > instruction)

| run | n | bare | prose | enforced | a-vs-c p | b-vs-c p |
|-----|---|------|-------|----------|----------|----------|
| flash s13 | 94  | 59.6% | 55.3% | 69.1% | 0.064   | 0.004   |
| flash s29 | 96  | 49.0% | 53.1% | 66.7% | 0.0002  | 0.015   |
| pro s13   | 98  | 50.0% | 52.0% | 72.4% | <0.0001 | 0.0001  |
| flash s47 | 191 | 50.3% | 50.3% | 67.0% | <0.0001 | <0.0001 |

Replicated 4×, two model tiers, up to n=191. Prose never beats bare (a-vs-b n.s. in
all four). Enforcement significantly beats both, robustly.

## Model-commodity 2×2 — seed-13 (v4-flash vs v4-pro)

Ran the same surgeon protocol on `deepseek-v4-pro` (stronger tier) on seed-13,
all 3 arms, clean scoring (no_report=0). Combined with the v4-flash seed-13 run:

| | bare (a) | enforced (c) |
|---|----------|--------------|
| **v4-flash** (cheap) | 59.6% | 69.1% |
| **v4-pro** (strong)  | 50.0% | **72.4%** |

v4-pro paired McNemar: **a-vs-c 3/25 p<0.0001**, **b-vs-c 3/23 p=0.0001**, a-vs-b 4/6
p=0.75. Enforcement engaged: 87 resolved / 11 recovery-exhausted closures; 40 tasks
used 2-4 recovery rounds; arm c had the fewest genuine failures (27 vs a 49 / b 47).

Three results from the grid:
1. **Commodity:** enforced-flash (69.1%) beats **bare-pro (50.0%)** by ~19 pts — a
   cheap model + enforcement outperforms an expensive model bare.
2. **Gap compression:** bare, flash > pro (60 vs 50); enforced, they converge (69 vs
   72). Enforcement makes the choice of model far less determinative — commoditization.
3. **Enforcement rescues the strong model from itself:** the "thinking" v4-pro
   over-elaborates *more* bare (50%, below flash), and enforcement lifts it to the
   top (72%) — the EXP1 over-elaboration mechanism, now on a stronger model.

Caveats: the flash and pro seed-13 runs are separate runs on the same task set (the
within-model a-vs-c comparisons are clean paired; the cross-model flash-c vs pro-a
commodity comparison is cross-run but same tasks). One sample so far — pro-seed29
will give a second. DeepSeek-only tiers; a cross-provider (gemini flash-lite) run is
in progress on separate hardware for breadth.

## Next (see task list)

Replicate (same everything) → generalize (different 100) → power (n=200–300 to settle
a-vs-c) → model transfer → the 2×2 model-commodity design. Then the feature-dev
benchmark, where enforcement should have even more headroom.

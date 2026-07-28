# EXP1 — Enforcement Ablation: Findings

**Status:** complete · **n:** 100 tasks (99 scored, 1 excluded) · **date:** 2026-07-07
**Coder:** `deepseek/deepseek-v4-flash` via opencode-cli · **Scorer:** SWE-bench Verified harness
**Run:** `exp1-20260707-180026` · wall time 6h43m

## Headline

The pre-registered hypothesis was **enforcement > instruction**: that running a task
through snodo's governed loop would resolve more SWE-bench issues than giving the
same methodology to the model as prose, which in turn would beat a bare agent.

The data do not support this, and on the one significant comparison they point the
other way. Prose instruction **outperformed** snodo enforcement (60.6% vs 50.5%,
paired McNemar p = 0.041). Enforcement showed no benefit over even the bare agent
(55.6%). The effect is genuine — not a harness artifact — and the mechanism is
identifiable: **spec-authoring induces the model to write larger, less-focused
patches, and larger patches resolve less often.**

An essential scope caveat (see Validity): the arm-c "enforcement" tested here is
snodo's **spec-authoring + governance scaffolding**, *not* its full
validate-and-recover loop, which was disabled for this run.

## Design

Three arms, run on the **same** 100 tasks (paired), stratified across 12 repos
(seed 13, ≤10 tasks/repo), single trial each (k=1, justified below):

- **Arm A — bare:** opencode agent on the raw problem statement.
- **Arm B — prose:** opencode agent given the snodo methodology as prose instructions.
- **Arm C — enforced:** the same task run through the snodo engine (intent protocol:
  spec-authoring, capability tokens, governed producer mode).

All three use the identical coder (opencode-cli + deepseek). Correctness is judged
externally by the SWE-bench Verified harness, not by snodo.

## Results

Resolve rate per arm (Wilson 95% CI):

| Arm | Resolved | Rate | 95% CI |
|-----|----------|------|--------|
| A — bare    | 55/99 | 55.6% | 45.7–65.0% |
| B — prose   | 60/99 | 60.6% | 50.8–69.7% |
| C — enforced| 50/99 | 50.5% | 40.8–60.1% |

Because the arms run on the same tasks, the correct test is paired (McNemar, exact,
two-sided) on the discordant pairs:

| Comparison | wins (X-only / Y-only) | concordant | p |
|------------|------------------------|------------|---|
| bare vs enforced (A/C)  | 10 / 5  | 84 | 0.30 (ns) |
| **prose vs enforced (B/C)** | **15 / 5** | 79 | **0.041** |
| bare vs prose (A/B)     | 6 / 11  | 82 | 0.33 (ns) |

The only significant paired difference is **prose beats enforcement**: of 20 tasks
where they disagreed, prose solved 15 that enforcement did not, and enforcement
solved only 5 that prose did not.

## The effect is genuine, not an artifact

Arm-c governance ran clean across all 99 scored tasks: **94 closed `resolved`, all
with `attempts=1`, zero spurious blocks or validator errors**. Of the 15 tasks prose
solved but enforcement did not, **14 are genuine wrong fixes** (non-empty patch, no
error, clean closure); only 1 was an empty patch. So enforcement's deficit is a
**quality** cost, not a reliability/crash cost.

(Earlier all-zero runs were traced to harness bugs — an old scipy vs numpy-2.0
incompatibility, a comma-joined `--instance_ids` argument swebench read as a single
ID, and a post-execute diff-read that inspected `HEAD~1..HEAD` while the coder left
changes uncommitted. All were fixed and verified before this run; see the run log.)

## Mechanism: over-elaboration

Patch size explains the gap. Median patch length: **A 909, B 1031, C 1523** — arm-c
produces the largest changes. And within every arm, size predicts failure:

| Arm | median len (resolved) | median len (failed) |
|-----|----------------------|---------------------|
| A | 737 | 2429 |
| B | 755 | 2285 |
| C | 999 | 2052 |

Minimal, focused patches pass; sprawling ones fail — in every arm. Spec-authoring
pushes the coder toward larger patches (its successful patches, median 999, are
already bigger than A/B's ~745), which lowers the resolve rate. In short:
**enforcement makes the model do more, and doing more resolves less** on single-shot
bug-fix tasks.

## Validity and limitations

- **What "enforcement" means here.** Arm C tested spec-authoring + governance
  scaffolding. snodo's post-execute output review was disabled for this run because,
  with the coder leaving changes uncommitted, the diff-read saw an empty diff and
  spuriously blocked (and, when softened to a warning, triggered pointless recovery
  loops). So this experiment does **not** test snodo's validate-and-recover loop —
  arguably its core value. The finding is bounded to spec-authoring scaffolding.
- **Multiple comparisons.** Three McNemar tests; a Bonferroni threshold is 0.017, so
  p = 0.041 is *suggestive*, not multiple-comparison-robust. The safe reading is
  "enforcement showed no benefit and trended worse, significantly so vs prose at the
  nominal level."
- **k=1 is justified.** A prior 10-task, 3-trial run was fully deterministic (every
  cell 0/3 or 3/3), so trials are replicas; budget went to more tasks, not repeats.
- **Single model, single-shot tasks.** One coder (deepseek) and SWE-bench's
  one-shot bug-fix format. Enforcement's value may surface on multi-step work or
  when output validation can catch and repair bad diffs — neither of which this
  design exercises.
- **n = 99.** Adequate to detect a large effect; underpowered for small ones.

## What EXP1b should test

The obvious follow-up is the enforcement loop this run could not: commit the coder's
changes before post-validation so the `review` validator sees a real diff, let it
bind, and let K-recovery re-author and re-run on a failed review. That tests whether
**validate-and-recover** — snodo's actual mechanism — recovers the tasks enforcement
currently loses, rather than spec-authoring alone. If it converts a meaningful share
of the 15 prose-only wins, the enforcement thesis is back in play; if not, the
over-elaboration cost dominates and the honest conclusion stands.

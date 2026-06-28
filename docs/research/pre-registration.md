# Pre-registration — EXP1 (enforcement) & EXP2 (model commodity)

Fixed BEFORE running. Every run snapshots experiments/config.yml into its
results for provenance. Changing a value = a new pre-registered config, not an
edit to results already collected.

## Dataset & selection
- SWE-bench_Verified (human-validated; ships difficulty annotations).
- Difficulty strata from Verified's own labels (<15min / 15min-1hr / >1hr),
  NOT patch-LoC. Proportional to the pool, or fixed counts in config.
- N tasks = HYPERPARAM (selection.n, pilot default 10), >= 5 distinct repos.
- Seed = 42.

## Sampling
- temperature = 0 for the pilot (reproducibility). Real-usage temperature is a
  later, separately-registered variant — recorded, never defaulted.
- k trials per (task, condition) = 3 for the pilot.
- Pinned model snapshots recorded per run.

## snodo recovery bounds (fixed for all experiment runs)
- max_recovery_depth = 3, max_total_fix_attempts = 10 (snodo defaults).
- Reported at this default; one-line sensitivity note only, NOT swept.

## Primary metric (pick one, lock it)
- PRIMARY: pass@1 = mean resolve rate over k trials (comparable to SWE-bench).
- SECONDARY: pass^k = all k trials resolve (reliability).

## EXP1 — enforcement
- Hypothesis: resolve(c snodo) > resolve(b prose) > resolve(a pure agent).
- Same base model all arms; arm b prose generated from the SAME protocol c
  enforces (parity gate). Report cost per arm too (snodo may win at higher $).
- Stats: Cochran's Q across 3 paired arms; McNemar pairwise (a-b,b-c,a-c) with
  Holm; per-arm Wilson 95% CI; effect sizes.

## EXP2 — commodity
- Hypothesis: commodity cells equivalent to E/E within the margin below.
- EQUIVALENCE_MARGIN = +/-10 pp  [YOURS TO SET: the largest resolve-rate gap
  you'd still call "equivalent for real use"]. TOST at this margin.
- Cost-vs-resolve frontier + fix-attempts-by-tier (mechanism).

## Power / why N
- 10 is a PILOT to estimate discordance/variance, not the confirmatory N.
- MIN_MEANINGFUL_EFFECT = 15 pp  [YOURS TO SET: smallest enforcement edge worth
  detecting]. After the pilot, compute the N for 80% power to detect that
  effect (McNemar), set selection.n, re-run as the confirmatory study.

## Validity controls
- Positive control: each task's GOLD patch must pass the oracle; if not, the
  harness is broken for that task -> drop it before scoring.
- Frozen oracle: FAIL_TO_PASS + PASS_TO_PASS fixed before any agent runs.
- Contamination: record created_at vs model cutoff per task; comparisons are
  relative/paired so shared contamination largely cancels.

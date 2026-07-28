# EXP1/EXP2 Runbook — Reproducing the Enforcement-Ablation Experiments

Everything needed to re-run the enforcement-vs-instruction and model-commodity
experiments. Findings live in `docs/research/exp1-findings.md`,
`exp2-findings.md`, `exp2-protocol-fit-analysis.md`.

## What the experiment is

Three arms run on the **same** SWE-bench Verified tasks (paired):
- **a — bare:** opencode agent on the raw problem statement.
- **b — prose:** opencode agent given the protocol methodology as prose instructions.
- **c — enforced:** the same task run through the snodo engine under a protocol.

The protocol for EXP2 is `bugfix-surgeon` (minimality: no spec-authoring; a
post-execute reviewer rejects sprawl; K-recovery re-tightens). Scoring is the
official SWE-bench harness (Docker, x86). Metric: resolve rate; test: paired
McNemar (arms share tasks).

## Environment (do this exactly)

**Never use `uv run` for runs or scoring** — it re-syncs the venv on every call
and has repeatedly corrupted numpy/scipy/openblas mid-run. Use the venv python
directly:

```bash
python3 -m venv .venv
.venv/bin/pip install -e packages/snodo-core -e packages/snodo-tools \
  -e packages/snodo-foundation -e packages/snodo-engine -e packages/snodo-mcp \
  swebench 'scipy>=1.13' 'numpy>=2' litellm pyyaml gitpython datasets typer textual
.venv/bin/python -c "import numpy, scipy.sparse, swebench; print('env OK')"
```

Pin `numpy`/`scipy>=1.13`/`swebench` so an accidental sync can't downgrade them.
Put temp/workspaces on a disk with space and off the root fs:
`export TMPDIR=/mnt/diskA/tmp`.

Docker: eval images are large (~1.5–4 GB each, layer-shared per repo). Move
Docker's data-root to a big disk if root is small. Scoring needs **x86** — ARM
boxes (e.g. GB10) can dispatch but cannot score.

One-time per machine: `snodo init` (needs a git repo present) generates the RS256
keypair arm-c requires, else arm-c fails `graph build failed: Public key not found`.

## Generate a task selection (frozen, reproducible)

```bash
.venv/bin/python -m experiments.tasks.generate_selection <N> <seed> <tag>
# -> experiments/tasks/<tag>.selection.jsonl  + <tag>.local.jsonl
```
Stratified by repo, per-repo cap 15%, seeded. Commit these files — they are the
backbone of reproducibility. Seeds used: 13, 29 (n=100), 47 (n=200).

## Run

```bash
export SNODO_EXP_PROTOCOL=bugfix-surgeon         # arm-c protocol (=b's prose)
export SNODO_VALIDATOR_MODEL=deepseek/deepseek-v4-flash   # litellm string for arm-c's
                                                 # classifier+validators (see note)
export TMPDIR=/mnt/diskA/tmp
.venv/bin/python -m experiments.run_exp1 --force \
  --selection   experiments/tasks/<tag>.selection.jsonl \
  --dataset     experiments/tasks/<tag>.local.jsonl \
  --results-dir experiments/results/<tag> \
  --set models.reference=<coder-model> \
  --set sampling.k_trials=1 \
  --set bounds.dispatch.max_parallel=<N> \
  2>&1 | tee <tag>.log
```
- `k_trials=1` — runs were deterministic (cells were 0/3 or 3/3); spend budget on
  more tasks, not trials.
- `max_parallel` — 4 for DeepSeek; **1–2 for rate-limited providers** (Gemini
  Tier-2 etc.).
- Isolation flags (`--selection/--dataset/--results-dir`) let multiple experiments
  run in one repo without clobbering. `SNODO_EXP_DATASET` is set from `--dataset`.
- `SNODO_SKIP_GOLD=1` — skip the per-task gold gate on a **dispatch-only** node
  (e.g. an ARM box that can't score); score later on x86 via `rescore_exp1`.

## Model transfer — the frictionless pattern

opencode (coder) and litellm (arm-c validators) use **different** provider strings
for the same model (opencode `google/…`, litellm `gemini/…`), and each needs its
own auth. To avoid per-provider litellm wiring when transferring the **coder**:
keep the enforcer fixed and cheap — `SNODO_VALIDATOR_MODEL=deepseek/deepseek-v4-flash`
(works for both opencode and litellm) — and only vary `models.reference` (coder).
This holds enforcement constant across coder models (clean comparison) and needs
zero new auth. Use it for gemini/gpt/claude/ollama-cloud coders.

## Scoring, re-scoring, analysis

Full patch text is saved in `results.jsonl`, so any run is **re-scorable** without
re-dispatching:

```bash
.venv/bin/python -m experiments.rescore_exp1 <results.jsonl> <out.jsonl>
.venv/bin/python -m experiments.analyze_exp1 <out.jsonl>   # rates + paired McNemar
```

**Score in chunks of ~40**, not one giant batch — `score_predictions_batch` times
out (2400s) on large batches and (historically) returned an N×N cross-product of
failures. Chunking avoids the timeout entirely.

## Gotchas we hit (and fixed)

1. **`uv run` env drift** → corrupts numpy/openblas. Use `.venv/bin/python`.
2. **`extract_patch` `.strip()`** removed the trailing newline → `patch
   unexpectedly ends in middle of line` → unapplyable. Fixed to keep one `\n`.
3. **Scorer timeout cross-product** → big batch times out, handler returned
   instances×ids (N²) all-fail, zeroing an arm. Fixed; also chunk batches.
4. **Comma-joined `--instance_ids`** → swebench wants space-separated. Fixed.
5. **Coder-created `venv/`/caches** swept into the diff → MB patches that don't
   apply. Excluded venv/cache/build dirs in extract + commit.
6. **litellm vs opencode model strings** → see the frictionless pattern above.
7. **Missing RS256 keypair** on a fresh box → `snodo init`.
8. **CPU-saturated scoring** (overlapping runs) → test timeouts / spurious fails;
   don't overlap scoring phases; chunk + cap `max_parallel`.

## Headline results (see exp2-findings.md)

Enforcement > instruction, replicated 4× (flash s13/s29/s47, pro s13), two model
tiers, up to n=191, all significant (a-vs-c & b-vs-c p ≤ 0.015; three p<0.0001);
prose never beats bare. Model commodity: enforced-flash (69%) > bare-pro (50%).

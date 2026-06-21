# Parallel validator execution

## Intent
Validators run sequentially today — 3 validators × ~4s = ~12s per
validation phase. They are fully independent (no inter-validator
dependencies, read-only workspace access, thread-safe completion_fn).
Running them in parallel reduces validation wall-clock time from ~12s
to ~4s — a 3× speedup on every task cycle.

## Acceptance criteria
- All validators in a validation phase run concurrently
- Results collected and returned in deterministic order
- context.model mutation is safe (copy context per validator thread)
- Policy evaluation receives all results same as before
- Wall-clock time for 3 validators drops from ~12s to ~4s
- All existing validator tests pass
- Pre-execute and post-execute validation phases both parallelised

## Constraints
- Read engine/validators.py (ValidatorRunner.run, _dispatch_one)
  and recon/__init__.py (ThreadPoolExecutor fan-out pattern — reuse
  this exactly)
- context.model is mutated per-validator — pass a copy of context
  to each thread, not the shared instance
- max_workers = min(len(validators), 4) — same as recon
- Results order must be deterministic for policy evaluation
- Do not change _dispatch_one or the validator interface
- Touch only engine/validators.py

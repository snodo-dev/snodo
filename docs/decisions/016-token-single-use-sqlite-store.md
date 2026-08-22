# ADR 016 — Shared SQLite store for validation-token single-use

## Status
Accepted

## Context
`TokenIssuer.verify_token` checked an in-memory `_used_tokens` set, but nothing
in production ever populated it — `consume_token` was only called from a test.
"Single use" was incidental housekeeping: the MCP server nulled its single
`_validation_token` slot and the engine cleared `validation_token` after
dispatch, but the JWT itself stayed valid until `exp`. Three `TokenIssuer`
instances (engine, MCP, module-level default) each held an independent set, so
a token burned on one path still verified on another, and any restart reset the
set. A governance tool that cannot enforce single-use of its authorisation
credential is fail-open.

## Decision
Single-use is enforced by a shared, user-global SQLite store at
`~/.snodo/tokens.db` (overridable via `SNODO_TOKEN_STORE`), created lazily on
first use — not at `snodo init` — so existing installs keep working.

- **Unit of single-use = one dispatch, not one tool call.** `verify_token`
  CHECKS the store (signature, exp, task binding, and the consumed set) but does
  NOT consume; a dispatch may involve many mutating tool calls. Consumption
  happens at the dispatch boundary (MCP `handle_dispatch_task`; engine
  `_execute_node`).
- **The INSERT is the claim.** `INSERT INTO consumed_tokens (token_id, task_id,
  exp) ...` with `token_id` as PRIMARY KEY gives atomic compare-and-set across
  processes; `IntegrityError` = already consumed. No read-then-write, no
  application-level locking.
- **Fail closed.** If the store cannot be opened/read/written, verification
  raises `TokenStoreError` and the run halts with a clear error + audit event.
  There is no "unsafe/skip" escape hatch. Recovery is `rm tokens.db`; blast
  radius is bounded to the TTL window because older tokens already fail on
  `exp`.
- **Secret handling.** An explicitly empty `SNODO_TOKEN_SECRET` is an error; an
  unset secret warns loudly (random per-process secret → cross-process tokens
  will not verify). MCP↔engine interop requires the shared secret AND the shared
  store — they ship together.
- The module-level `_default_issuer` and its `issue_token`/`verify_token`/
  `decode_token` wrappers are removed (no un-shared consumed-set remains).

## Consequences
- `consume_token` is now called from production (both dispatch boundaries).
- The store is pruned opportunistically (`DELETE ... WHERE exp < now`) on open
  and every N inserts; no scheduler.
- A new `SNODO_TOKEN_STORE` env var lets read-only-FS deployments point the
  store at a writable location.

## Alternatives considered
- In-memory set (status quo): rejected — not shared across processes, resets on
  restart.
- Project-relative store (`<project>/.snodo/`): rejected — MCPs root at
  `worktree_path` during task execution, fragmenting the store across worktrees.
- Reusing `checkpoints.db`: rejected — that schema belongs to LangGraph's
  SqliteSaver.

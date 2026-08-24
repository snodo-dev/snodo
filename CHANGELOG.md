# Changelog

All notable changes to snodo will be documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
snodo uses [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

### Added

- `snodo run` now prints node transitions on the normal path — entering
  pre-execute validation (with the validator list), coder dispatched/returned,
  and post-validation — instead of going silent between "Graph compiled" and the
  halt payload. `--verbose` additionally prints each validator's verdict as it
  lands, rather than only at the end. The information was already in the graph
  state; it is now surfaced while it matters.

- The `snodo init` trusted-repository consent gate is now rendered as a styled,
  amber-bordered panel ("Trusted repository") with a footer pointing at
  `ADR 014 · SECURITY.md`, and the bare `input()` is replaced with a Rich-styled
  confirmation defaulting to **No**. Output degrades to plain text when not a TTY,
  when `NO_COLOR` is set, or when piped. `rich` is now an explicit root dependency.

### Changed

- `snodo` is now a PEP 420 namespace package: the six top-level `snodo/__init__.py`
  files (one per workspace package) are removed, and the version is exposed via
  `snodo/version.py` (`from snodo.version import __version__`). This makes the
  `.importlinter` package-layering contracts actually runnable — grimp's
  `find_spec` previously resolved only the first `__init__.py` and reported every
  other package's modules as missing, so `uv run lint-imports` had never passed.
  With the namespace package in place the gate runs against the working tree and
  found one real violation, now fixed: `snodo.jobs.wrapper` imported
  `snodo.cli.main` in-process (mcp layer depending on the app layer); it now
  invokes the CLI as a subprocess.

- The protocol-template registry (`PROTOCOL_TEMPLATES`) is now derived from the
  YAML files in `snodo/protocols/templates/`, so adding a template file (e.g.
  `greenfield.yml`) makes it selectable with no second edit. Templates are parsed
  and verified (WF1–WF5) at import time, so a broken shipped template fails
  loudly in CI rather than at a user's first `init`. `snodo init` now resolves and
  verifies the template before creating `.snodo/`, touching `.gitignore`, or
  writing project identity, so a failed init leaves the directory as it found it.
  An unknown `--template` exits with a clear error listing the available
  templates, and the interactive menu is generated from the registry (re-prompting
  on invalid input instead of substituting a different protocol).

- WF1 no longer requires total tool disjointness. It now enforces that
  approval-conferring tools (`approve`, `merge` by default, extendable via a
  protocol-level `exclusive_tools` set) appear in at most one mode, which is the
  property that actually prevents self-approval. Non-exclusive tools (e.g. `edit`)
  may be shared across modes, so staged protocols that both need `edit` now load.
  INV2 (capability bounded by the active mode) is unchanged. Because mode can no
  longer be inferred from the tool alone, every `tool_call` (and `wf1_violation` /
  `dispatch_request`) audit entry now records the active mode via
  `mcp/server.py:_active_mode()`. See ADR 017.

### Fixed

- The post-validation recovery loop no longer feeds raw validator
  justifications into the fix task. It previously built the recovery spec as
  `"Fix post-validation issues: " + "; ".join(justifications[:3])`, truncated to
  500 chars — a state description written for a human reading a report, cut
  mid-sentence, that the spec validators then rejected for lacking intent,
  constraints and acceptance criteria. The recovery spec is now synthesised with
  an explicit INTENT (the original task), CONSTRAINTS, and per-failure
  ACCEPTANCE CRITERIA, with each justification preserved verbatim as context
  rather than truncated.

- `snodo init` now commits `.gitignore` after adding the `.snodo/` entry. An
  untracked `.gitignore` is itself a `git clean -fd` target, so two consecutive
  cleans would remove `.gitignore` and then the now-unignored `.snodo/` —
  destroying the project id, session store and audit chain (INV4) with no
  warning. Only `.gitignore` is staged and committed; unrelated staged or
  unstaged changes are left untouched. When the commit cannot be made (no git
  identity, unborn branch, hook failure), init still succeeds but warns that the
  ignore is not yet durable.

- `snodo run` now honours the active mode. It previously passed
  `protocol.initial_mode` to the closure driver (and to agent-memory
  `record_task`) while `_resolve_session` correctly used `state.current_mode`,
  so on a multi-mode protocol `snodo mode change <m>` had no effect: every run
  executed the initial mode's validators and reported success against the wrong
  gate. The active mode is now resolved once (from `state.current_mode`, falling
  back to `initial_mode`) and threaded through the whole run path, and a session
  whose mode disagrees with the active mode is now a hard error rather than a
  silent mismatch. Single-mode protocols are unaffected.

- The MCP `validate_task` handler no longer injects an unconditional pytest
  `test_runner` result into the validator quorum. Because `PolicyEvaluator` derives
  `total_count` from the number of results, the injected entry inflated the
  denominator on the MCP path only — a protocol declaring three validators was
  evaluated as four, so `unanimous` additionally required the test runner to pass
  and `quorum` moved from 2/3 to 3/4. The engine path never injected it, so the two
  paths could reach different decisions for the same protocol. The hardcoded
  `tests/` path also meant a project without that directory could never pass
  validation. Tests now participate only when the protocol declares a
  `quality`/test validator, resolved through the shared runner like every other
  validator. The engine/MCP parity test now asserts equal policy decision and
  `total_count`, not just per-validator severities.

- A validator that crashes is no longer silently downgraded. `severity_cap` was
  rebuilding the `ValidatorResult` without carrying the `error` flag, so
  `PolicyEvaluator`'s fail-closed `error_count > 0 → HALT` path was bypassed: under
  `severity_cap: pass` a crashed validator became a `pass` and a token was issued,
  and under `severity_cap: warn` (used by the shipped `bugfix-surgeon` and
  `feature-warden` protocols) it was reported as a human-adjudicable `escalate`
  rather than `validator_error`. Error results are now never capped — a crash is an
  operational fault, not a severity judgement. The duplicate capping implementation
  in `engine/loop.py:_default_validator` now delegates to the shared runner, so
  capping exists in exactly one place.
- Task identifiers are stable across processes. `task_id` was derived from Python's
  `hash()` of the description, which is salted per interpreter, so the same
  description produced a different id on every run; `& 0xffffff` also truncated it
  to 24 bits, making collisions likely in the low thousands of tasks. Ids are now a
  48-bit SHA-256 digest via a single `derive_task_id()` helper in `snodo-core`,
  called from both `run_cmd` and `job_cmd`. Ids stored in existing sessions still
  resolve — `--retry` reads them rather than recomputing — so no migration is needed.

---

## [0.6.0] — 2026-08-21

Hardening pass over the enforcement core: five fail-open or silent-failure
defects, plus concurrency and durability fixes. Tracked as issues #5–#15;
decisions recorded in ADRs 014–016.

### Fixed

- The Kleene-closure driver (`run_to_closure`) no longer reports graph
  exceptions (coder/network/DB failures) as `resolved`. `resolved` now requires
  positive completion evidence (`is_complete`); a graph exception, a non-dict
  result, or a missing completion signal is reported as `internal_error` with a
  non-zero exit code and a `recovery_internal_error` audit event. LangGraph
  control-flow exceptions (`GraphInterrupt`/`GraphBubbleUp`) are propagated to
  the caller rather than swallowed.
- `snodo run` now emits the `--- STRUCTURED HALT PAYLOAD ---` block on the
  closure path (the primary execution path). The payload is built by the engine
  (single source of truth) and has `final_decision` equal to `halt_type`, using
  the canonical vocabulary (`escalate` / `blocker` / `validator_error` /
  `internal_error`). `validator_error` and `internal_error` no longer advise
  `snodo authorize`. The legacy single-invocation stream path was removed.
- Validation-token single-use is now enforced by a shared SQLite store
  (`~/.snodo/tokens.db`, overridable via `SNODO_TOKEN_STORE`). `consume_token`
  is called at the dispatch boundary (engine + MCP); the INSERT is an atomic
  claim across processes and survives restarts. Verification fails closed if
  the store is unavailable. An empty `SNODO_TOKEN_SECRET` is an error; an unset
  secret warns loudly.
- The audit log no longer silently truncates on load. `AuditLog` now raises
  `AuditError` (naming the offending line and log path, with recovery guidance)
  on a malformed line, hash mismatch, or sequence discontinuity instead of
  returning a partial list. Appends are refused onto an unverified chain, and
  `verify_chain()` now also checks that the on-disk log agrees with the
  in-memory chain, so a forked or truncated chain is never certified (INV4).
- Session writes are now atomic: `SessionManager._save_session` serialises to a
  same-directory `.tmp` file and `os.replace`s onto the target, so a crash
  mid-write leaves the previous session intact (INV5). Corrupt session files are
  no longer skipped silently — enumeration warns and audits (`session_corrupt`),
  and a corrupt *active* session raises `SessionError` instead of silently
  adopting a different session. State-file writes across `memory.py`, `recon`,
  and `jobs` now use `os.replace` (atomic overwrite on all platforms) instead of
  `os.rename`.
- The token store no longer fails with `database is locked` under concurrent
  dispatch. `busy_timeout` is now the first PRAGMA on every connection; the
  one-time DDL (WAL + `user_version` + `CREATE TABLE`) is serialized in-process
  and made idempotent (WAL and `user_version` are only written when they differ),
  and a failed `consume` (already-consumed token) now rolls back its implicit
  write transaction instead of holding the write lock. Concurrent consumers of
  the same token now resolve to exactly one winner without exceptions.
- The Kleene-closure driver no longer lets one over-deep sibling cancel the
  rest. A per-branch `max_recovery_depth` violation now records the exhausted
  child and `continue`s to the next sibling instead of `break`ing and zeroing
  the global `max_total_fix_attempts` budget. The parent is marked
  `recovery_exhausted` when any sibling is depth-exhausted (the closure is
  incomplete), but unrelated legal siblings still execute and consume exactly
  one budget unit each; genuine global exhaustion still stops processing.

---

## [0.2.0] – [0.5.4]

Released without individual changelog entries. See the git history
(`git log v0.1.0..v0.5.3`) for detail. Highlights across this range: the MCP
server (`snodo serve`), the TUI dashboard, the opencode coder adapter, the
Kleene-closure recovery loop (ADR 013), git-worktree task isolation, cloud audit
sync, background jobs, and the AGPL-3.0 → Apache-2.0 relicence.

> **Note on versioning.** The `v0.9.0-tosem` tag and the `version` field in
> `CITATION.cff` describe the archived research snapshot deposited to Zenodo
> (DOI [10.5281/zenodo.21967946](https://doi.org/10.5281/zenodo.21967946)) —
> not a package release. Package versions and the citable artifact version are
> tracked on separate axes and are expected to differ.

---

## [0.1.0] — 2026-06-01

Initial public release.

### Core protocol engine
- Mode-based capability separation (producer, reviewer, planner)
- Validator quorums with disagreement policies (unanimous, majority, quorum, any)
- JWT-backed single-use validation tokens (WF1–INV5 invariant set)
- Session resumability with file-backed checkpointing
- ESCALATE resolution: halt → resolve → resume pattern
- Constraint predicate framework with two-phase evaluation
- Protocol adherence validator deriving mode profiles from operational primitives

### Protocol templates
- `solo`, `team`, and `2+n` templates ship with the package

### Interfaces
- CLI (`snodo`) with full command surface
- MCP server (`snodo serve`) for AI agent integration
- TUI dashboard (`snodo dashboard` / `snop`)

### Studies
- Policy Monte Carlo study
- Detection probability study
- Overhead benchmarks
- Byzantine robustness study

---

[0.1.0]: https://github.com/snodo-dev/snodo/releases/tag/v0.1.0

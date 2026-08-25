# Changelog

All notable changes to snodo will be documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
snodo uses [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

### Added

- Automatic environment preparation before task execution. Snodo detects the
  repository ecosystem (npm, pnpm, yarn, bun, uv/pip, cargo, go) from lockfiles
  and markers in fresh worktrees and runs the appropriate lockfile-frozen install
  command prior to task execution. Protocols can explicitly declare or disable
  this via `execution.prepare_command`. Installation failures halt as an
  operational fault (`validator_error`) without entering recovery. Process-safe
  global caches (e.g. `~/.npm`, `~/.cache/uv`) are leveraged while avoiding unsafe
  uncached worktree sharing. (Fixes #26, ADR 023).

- A versioned machine interface for integrations. `status`, `mode show`,
  `session show`, `task show`, and `worktree list` gain a `--json` flag that
  emits a single JSON object to stdout (human output is unchanged), and a new
  `snodo validate <task_spec> [--phase pre_execute|post_execute]` command runs
  a phase's validators through the shared engine runner and returns the
  four-outcome result — the halt-payload shape, reachable directly, with no
  coder. Every payload carries a `schema` field (`snodo.<command>.v1`) so a
  consumer can detect a breaking change, and `snodo validate` uses exit codes
  that distinguish the four outcomes (`pass`=0, `blocker`=1, `escalate`=2,
  `validator_error`=3, `internal_error`=4). The contract is documented in
  `docs/machine-interface.md`. See ADR 022.

- `snodo run` can now merge a successfully completed task's branch into the
  base branch (opt-in). Set `execution.auto_merge: true` on a protocol, or
  `auto_merge: true/false` on a mode to override the protocol default. The base
  branch is resolved from the repository (remote default, falling back to
  `main`) instead of being hardcoded, in both worktree creation and
  `merge_branch`. A merge conflict escalates (leaving the branch and worktree
  intact for a human) rather than crashing or blocking, and the worktree +
  branch are cleaned up only after a successful merge. See ADR 018.

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

- The spec-authoring rewriter now receives only spec-quality critique. A
  `Validator` gains a `judges_spec` flag (set on the shipped `meta-spec` and
  `spec-manners` validators); only those validators' critique reaches the
  author. A non-spec validator's objection (architecture, security, ...) is
  about the work, not the wording, and no longer gets laundered into the spec —
  previously a validator could block on a stale criterion, have its objection
  written into the rewrite, then block on that same sentence again. If the only
  escalation is from non-spec validators, the task escalates normally instead
  of running a pointless rewrite. The halt payload now carries a `spec_authoring`
  provenance block (attempt, triggering validators, original spec, authored
  spec) so a blocker's origin is visible. See ADR 023.

- LLM validator prompts are now phase-aware. The judge is told whether it is
  reviewing a proposal (pre-execute) or inspecting a finished result
  (post-execute), so a tool-enabled pre-execute validator can no longer read
  "evaluate the task against the criteria" as "check whether this was done" and
  block on work that cannot exist yet. The phase frame is written into the
  engine, not into each protocol author's criteria. See ADR 019.

- The `solo`, `team`, and `2+n` templates now grant `read_file` and `list_files`
  to their `security` and `architecture` validators (and `conventions` in
  `2+n`), whose criteria are phrased as facts about the repository. `meta-spec`
  still gets no tools — it judges the spec, and the spec is all it should see.
  The read-only allowlist is unchanged. See ADR 019.

- Wave classification now reads its budget and temperature from
  `llm.classifier` instead of `llm.wave`. `llm.classifier.max_tokens` and
  `llm.classifier.temperature` were inert — the classifier call read `WaveConfig`'s
  accidental copies, so raising the budget under `llm.classifier` had no effect.
  `WaveConfig` now keeps only `max_age_days` / `max_idle_days`. The classifier
  model is resolved exactly once, so the completion function's bound model and
  api_base can no longer disagree with the model passed to the call. The
  duplicated classification path in `loop.py` and `governance.py` is collapsed
  into one. See ADR 020.

- `llm.wave.max_tokens` / `llm.wave.temperature` are migrated to
  `llm.classifier` on load, with a deprecation warning naming the new keys —
  they were the only working classifier knobs, so they are moved rather than
  silently dropped. `snodo config set` now accepts `classifier.*` and the
  remaining `wave.*` keys, and rejects the deprecated wave keys with a pointer
  to the new name. See ADR 020.

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

<<<<<<< HEAD
- Audit log loading errors (`AuditError`) during dashboard data provider
  instantiation and `snodo cloud sync` are now caught gracefully. Corrupt
  audit logs no longer crash the TUI dashboard (which degrades by omitting
  audit event details) or `snodo cloud sync` (which reports the corrupt log
  per session and returns failure status). (Fixes #14).
=======
- Worktree isolation is no longer lost silently on a repository with no
  commits. On an unborn HEAD the base branch does not resolve and
  `git worktree add` fails; snodo previously degraded to running the agent
  directly in the operator's working tree with only a warning — on the state
  every greenfield repository starts in. `create_worktree` now detects the
  unborn HEAD and raises `WorktreeIsolationError` with actionable guidance
  (make an initial commit, or pass `--no-isolation`), `setup_for_task` no
  longer swallows worktree-creation failures into a degraded run, and
  `snodo run` aborts unless `--no-isolation` is passed explicitly, recording
  a `worktree_isolation_failed` audit event. Background jobs are refused up
  front (marked failed, `submit()` raises) rather than spawned un-isolated.
  The new `snodo run --no-isolation` flag is the only way to accept a
  degraded run. Fixes #29, ADR 025.
>>>>>>> task/task_trunc/huge-task

- A truncated coder response is now handled as an execution failure with outcome
  `internal_error` naming the token ceiling that was hit and stating the task is
  too large, rather than falling through to zero artifacts and passing
  post-validation against an unchanged worktree. The failure reason reports how
  much content/tokens were generated before truncation so an operator can judge the
  task size. Truncation finish_reasons (`length`, `max_tokens`, `MAX_TOKENS`) are
  now unified across all adapter classes so no provider's truncation response is
  silently ignored. Post-validation is skipped on truncation. Fixes #39.

- Recovery now builds from the original task, not the previous attempt. The
  fix-task id is numbered linearly off the root (`task_X_fix_1`, `_fix_2`,
  `_fix_3`) instead of nesting (`task_X_fix_1_fix_1_fix_1`), and the recovery
  spec carries the original intent once plus the accumulated failure list —
  each failure attributed to the attempt that produced it — instead of wrapping
  the previous spec (which `meta-spec` rejected at depth 3 as "repeated
  recursive text"). The quality validator now emits the bounded stdout/stderr
  tail on a genuine test failure rather than a one-line summary, so the fix
  task receives the same evidence the validator captured (test name, assertion,
  file) instead of an invisible "Tests failed (exit 2): }". A recovery attempt
  that produces a validator verdict identical to the previous attempt's now
  halts as `recovery_stalled` (a blocker) instead of exhausting depth. See
  ADR 021.

- A task that does not complete now keeps its worktree instead of destroying
  the only copy of its evidence. `run_cmd.py` removed the worktree in an
  unconditional `finally`, so a truncated or unparseable coder response — which
  commits nothing — was deleted with no branch content left to rebuild from.
  The worktree is now torn down only on a cleanly resolved task (auto-merge is
  unaffected), and otherwise preserved with output naming the path, the branch,
  and how to inspect/remove it. `snodo run --retain-worktree` keeps it even on
  success. Retained worktrees are prunable via the new `snodo worktree`
  command (`list` / `remove <task_id>` / `prune`), where `prune` defaults to the
  protocol's `execution.branch_ttl_days`, so nothing accumulates silently. The
  background job wrapper no longer removes a failed task's worktree either.

  Uncommitted work is deliberately **not** committed to the task branch on
  teardown: the preserved worktree keeps it on disk for inspection, and
  auto-committing would mark possibly-garbage coder output as a commit on the
  branch. An operator who wants the work commits it by hand (or discards it with
  `snodo worktree remove`); a worktree is never deleted and never silently
  accumulated.

- The `quality` validator now reports operational faults as `validator_error`
  (a `blocker` result with `error=True`), not as judgements about the work. A
  missing test command, a command not found (exit 127), a non-executable
  command (exit 126), or a timeout previously landed on the judgement path —
  reaching a human as adjudicable and entering the recovery loop, which spent a
  coder call and a full validator quorum per depth level trying to code around
  a missing binary. These are distinguished by evidence (the shell's reserved
  exit codes corroborated against stderr, plus `FileNotFoundError` /
  `PermissionError` / `TimeoutExpired`), not by exit code alone; a genuine
  non-zero test result remains a blocker. The failure message now names what is
  missing and how to set it (`tooling.test_command`), and carries a bounded
  tail of the command's stdout/stderr. The closure driver never spawns a
  recovery subtask for `validator_error` or `internal_error`.

- A failed execution step is no longer validated and no longer reported as a
  blocker. Previously `execute` had an unconditional edge to `post_validate`,
  so a run where the coder produced nothing went on to pass post-validation
  against an unchanged worktree — a green verdict on zero artifacts. The
  execution step now routes a failure straight to the terminal halt, the
  payload marks `post_validation.outcome` as `skipped` (not `passed`), and the
  failure reason reaches the payload's top-level `reason`. `execution_error`
  was also mapped to `halt_type: "blocker"`; per ADR 015 an operational fault
  is `internal_error`, so it now reports `halt_type == final_decision ==
  raw_halt_type == "internal_error"`, and the halt payload's `raw_halt_type`
  always equals `halt_type` so no member of the four-outcome vocabulary can be
  silently remapped to another.

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

# Changelog

All notable changes to snodo will be documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
snodo uses [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

### Added

- The opencode coder path is now explicitly **experimental**, not supported.
  The containerised `opencode` and host `opencode-cli` backends are exercised
  by the adapter conformance suite and the `.snodo/` guard and commit hold for
  them (ADR 027/030), but they do not yet report per-turn progress or
  contribute usage/cost records, and no shipped template uses them. The
  position is recorded where an operator meets the path: `docs/protocol.md`
  (the `coder` field and a "Coder backends" section), `docs/architecture.md`,
  the `snodo init` Docker check output, and the minimal-webapp runbook. See
  ADR 034.

- Tool loop repeat read memory deduplication in `LiteLLMCoder` and `LLMValidator`. When a model
  repeatedly requests files or line ranges already fetched in an earlier turn (e.g. `read_file`
  at Turn 3 and Turn 19), the tool loop intercepts the request and returns a turn pointer
  (`"'<path>' was already fetched using <tool> in Turn N. Refer to the tool response from Turn N"`)
  without re-executing disk reads or duplicating large file payloads in the transcript. See ADR 033. (Fixes #64).

- A deterministic canary for the `acceptance` validator (Fixes #59): a judge
  driven by the validator's real tool loop against a real tree rejects a real
  omission — a task whose acceptance criterion demands a test (or a decision
  record) that the produced artifacts demonstrably lack returns `warn` and
  names the unmet criterion. The mirror case is proven too: an uncheckable
  criterion (device behaviour, human judgement) returns `pass`, so the safe
  direction is established rather than assumed. Previously the validator had
  never been observed rejecting anything — the standing finding was that every
  read-only judge passes everything it is shown.

- `snodo run` now tells the operator when their project's validator set is
  out of date. Adding a validator to a shipped template does NOT add it to a
  project whose `.snodo/protocol.yml` predates the change, so a pre-ADR-028
  project silently keeps running without `acceptance`. The notice is printed
  at load time (same failure pattern as a validator that does nothing —
  absence indistinguishable from success). (Fixes #59).

- Patch / diff coverage enforcement (`snodo.infrastructure.patch_coverage`). Test coverage is
  now measured over added/modified lines in git diff (`<base_ref>..HEAD`) rather than relying
  solely on a repository-wide global percentage (`--cov-fail-under=63`), preventing 0%-coverage
  modules from merging undetected. Both global repository threshold and patch coverage threshold
  (>=80%) are enforced in CI. See ADR 032. (Fixes #61).

- First-class `verification_executed` audit trail events. Verification command executions
  (what command ran, against which commit hash, return code, outcome, and output evidence)
  are now recorded as immutable events in `.snodo/audit.log`. Automatic merges refuse to land
  unverified work (`unverified_merge_blocked`) if no passing verification event is present
  in the audit trail for the commit. See ADR 031. (Fixes #60).

- Verification gate canaries for `import-linter`, `ruff`, `e2e`, and `toolchain pin`,
  establishing the standing rule that a new verification gate ships with a canary
  test proving it can fail when violations are injected. (Fixes #58).

- A post-execute `acceptance` validator that judges the produced artifacts
  against the acceptance criteria in the task spec. Previously the pipeline
  verified that the repository still works (`quality` runs the test suite) but
  nothing verified that the task was carried out — a coder that did part of the
  job (no test for the new feature, no ADR recording a decision the code now
  contradicts) passed every validator and auto-merged. The acceptance
  validator reuses the LLMValidator tool loop and is shipped in the `solo`,
  `team`, `2+n`, and `greenfield` templates with `severity_cap: warn`, so a
  miss routes to recovery (the coder can fix it) rather than a hard halt. It
  distinguishes "unmet" (verifiable from the tree and demonstrably absent)
  from "uncheckable" (device behaviour, human judgement — never a finding), so
  it cannot block good work on criteria it cannot verify. It judges
  completeness against the spec, not correctness of the code — it never runs
  commands. The produced artifacts are threaded to the validator context via
  `run_validators(artifacts=...)`. See ADR 028. (Fixes #54).

- Per-mode `max_recovery_depth` overrides on `Mode` (`mode.max_recovery_depth`),
  following the resolution pattern `auto_merge` already uses (ADR 018): a mode
  value overrides the protocol default, and a silent mode inherits it. The
  lookup is shared rather than duplicated, via `Protocol.resolve_mode_setting`.
  The reason greenfield wanted a low budget was always a per-phase reason, so
  `greenfield.yml` now sets `max_recovery_depth: 1` on `decide` and `scaffold`,
  where failures are setup and context faults recovery cannot fix, and `3` on
  `build`, where the harness is verified and failures are the shape recovery is
  good at. Protocol authoring documentation updated. See ADR 029. (Fixes #40).

- Test coverage and end-to-end integration tests for the plan execution path
  (`_run_plan` and `snodo run --plan <name>`). Covers happy path multi-wave
  plan execution, resume after partial completion, wave filtering (`--wave`),
  interactive task skipping, and failure modes including missing task spec files,
  dependency blocking, task execution failure, invalid wave filters, and
  planner errors. (Fixes #44).

- Real-time progress observability for LLM tool loops during coder and
  validator execution. Elapsed time (`[m:ss]`) and turn tool call summaries
  (e.g. `Turn 1: read_file(...)`, `Turn 2: submit_files(2 file(s))`) are printed
  synchronously on real tool-loop turn events without control characters,
  spinners, or fake heartbeats. (Fixes #51).

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

- CI now runs on every branch push (`push: branches: ['**']`) instead of only
  on `main`, and a new `snodo merge <branch>` command gates the merge on the
  branch's latest CI conclusion. Previously CI triggered on `push: [main]` and
  `pull_request: [main]`; agents merge locally and push the merge to main, so
  CI ran *after* the merge was already on main and the pull_request trigger
  never fired — CI was a post-mortem, not a gate. The branch-triggered shape
  was chosen over PRs to keep local merges. `snodo merge` queries
  `gh run list --branch <branch> --workflow ci.yml` and refuses to merge when
  CI has not run, is in progress, or has failed; "CI has not run" is a distinct,
  visible state, never confused with "CI passed". `--force` bypasses the gate
  for a human who has verified the branch by other means. (Fixes #56).

- Evaluated and confirmed `max_recovery_depth: 3` default balance and explicitly
  declared recovery budgets (`max_recovery_depth: 3` for established templates, `1` for greenfield)
  across all shipped protocol templates with explanatory comments. Documented the execution
  configuration and depth tradeoff in `docs/protocol.md`. (Fixes #40).

- The halt hint now names only the fix targets that apply to the halt in hand.
  A blocker has three fix targets — the code, the spec, or the policy — and
  every blocker previously emitted the same "re-run a revised task" line,
  which pointed only at the task even when the block was a stale criterion or a
  missing tool grant. The hint is now derived from the halt: a protocol
  violation (`constraint`, `wf3`) is a policy problem; a loop that never
  converged (`max_iterations`, `recovery_exhausted`, `recovery_stalled`) is a
  spec or policy problem; a post-execute rejection of produced artifacts is a
  code problem; a pre-execute rejection of the proposal is a spec problem. When
  a blocker cites a criterion, the hint names that criterion and points at
  `.snodo/protocol.yml` as a legitimate place to fix it (the criterion may be
  stale or a tool grant may be missing). A hint that lists all three every time
  is no better than one that names one, so only the targets that apply are
  named. (Fixes #38).

- The spec authored by the spec-authoring rewriter is now surfaced live when
  it is produced. When pre-execute validation escalates on warn-only
  spec-quality critique and the engine rewrites `loop_state.task.spec`, the
  run prints the attempt number, the triggering validators, the original
  spec, the authored spec, and the critique at that moment — so someone
  watching a run sees that their words were replaced where it happens,
  instead of only discovering it by reading the halt payload afterwards. The
  provenance was already carried in `metadata["spec_authoring"]` and the halt
  payload; this surfaces that existing state synchronously. (Fixes #36).

- Legible criterion text in halt payloads for index citations. When a validator
  justification cites criteria by index (e.g. `criterion 3`), the cited
  criterion text is excerpted directly into the justification string and
  populated in a structured `cited_criteria` field on `ValidatorResult`.
  Uncited results carry no extra overhead, keeping payload sizes minimal while
  making halt payloads self-contained without needing to open `.snodo/protocol.yml`.
  (Fixes #37).

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

- A run can no longer report two outcomes. Previously a run that emitted a
  complete structured halt payload (halt_type, final_decision and raw_halt_type
  all set and agreeing) could then print "✗ Internal error during execution:
  unknown internal error" — one run, two outcomes, the second unclassified.
  The leak was in `_report_closure`: after emitting the authoritative payload
  it also ran the legacy `tree.outcome == "internal_error"` fallback, and in a
  recovery chain the root's `final_state` carries no `error` field (the error
  lives in the subtask's payload), so the fallback printed the generic
  message. The structured payload is now the single emission site: when it is
  present, its `final_decision` is the only outcome reported. The legacy
  classification runs only when no structured payload exists (a failure
  outside the graph). (Fixes #66).

- The coder truncation diagnosis now reports what was observed and labels
  inference as inference. "Coder output truncated at max_tokens=64000: task
  is too large" asserted a cause that was not observed: the same finish_reason
  occurs when a tool call's arguments exceed the output budget and are cut off
  mid-argument — a different fault with a different remedy. The message now
  states the observed facts (finish_reason, generated token/char counts) and
  presents "the task is too large, or a tool call's arguments were cut off
  mid-argument" as an inference, not a confirmed diagnosis. (Fixes #67).

- The test suite no longer sleeps in real time where a fake clock works.
  `snodo serve --tunnel` waited a hardcoded 2s for uvicorn to bind
  (`_run_tunnel`), `JobManager.wait_for` polled with a real-time 1s sleep, and
  the token reissue test slept 1.1s to force a different JWT `iat` (which has
  1-second granularity). Sleeping does not parallelise away — the worker sits
  idle — so the ~6s of serial sleep survived `-n auto`. All three sites now
  take an injected clock: `serve_cmd._wait_for_server_bind(sleep_fn=...)`
  (resolved at call time), `JobManager.wait_for(clock=...)`, and
  `TokenIssuer(now_fn=...)`. Tests inject fake clocks and run in milliseconds.
  (Fixes #63).

- `tests/e2e/test_init_project_id.py` ran in every fast pass (~10-40s of
  suite time) because it lives in `tests/e2e/` but carried no `e2e` marker, so
  the default addopts (`-m 'not e2e'`) did not exclude it. It is now marked,
  and a collection hook in `tests/e2e/conftest.py` fails the run if any test
  under `tests/e2e/` lacks the marker, so an unmarked e2e test cannot silently
  re-enter the fast pass again. (Fixes #62).

- Post-execute validators can no longer review the previous commit instead of
  the change. The container `opencode` adapter (the Docker/HTTP path) wrote to
  the volume-mounted workspace in place but never committed, so `HEAD` did not
  move and validators that read `git diff HEAD~1..HEAD` (the "## Code Change"
  block in `llm_validator` / `acceptance`) reviewed the PREVIOUS commit —
  a confident review of the wrong change that then passed. Committing is now
  owned by `InPlaceCoderAdapter` (the same base class that owns the `.snodo/`
  guard, ADR 027): after the coder runs, the base class stages and commits the
  working tree with an explicit identity, so the git review channel and the
  returned `CodeArtifact` always describe the same change.
  `OpenCodeCLIAdapter`'s per-adapter commit is folded into the base class and
  the duplicated git readback is removed. A conformance test parameterised
  over every registered coder adapter asserts that an adapter's change is
  observable (a non-empty `CodeArtifact`), attributable (the reported paths
  exist on disk), and reviewable through `HEAD~1..HEAD` — the seam that let
  this drift (an ABC, two `skip_*` booleans, `hasattr` duck typing) now fails
  a test at the branch instead of surfacing months later. See ADR 030.

- Replaced the intrusive `UserWarning` on unset `SNODO_TOKEN_SECRET` with debug
  logging. Generating a random per-process secret is expected and secure for
  single-process CLI execution, so raw warnings with stack traces no longer
  surface at startup. (Fixes #42).

- Registered `PolicyAction` with LangGraph's msgpack serializer (`SAFE_MSGPACK_TYPES`
  and checkpointer `JsonPlusSerializer` allowlist). Deserializing policy actions
  from checkpoints no longer prints deprecation warnings and is safe against
  future strict deserialization enforcement. (Fixes #43).

- Corrected documentation references from `snodo resolve` to `snodo authorize`.
  The CLI registers `snodo authorize` (review and RS256-sign pending decisions),
  whereas `snodo resolve` was an outdated pre-RS256 draft syntax. Also fixed
  `snodo config set` / `snodo config get` documentation signatures in
  `docs/runbook.md` to use dot-separated keys (`snodo config set <key> <value>`),
  fixing an extra-argument crash when following the runbook examples. (Fixes #22).

- The coder and validator tool loops no longer send a malformed message
  history after a terminal tool call. Previously, when `submit_files` was
  called with zero files or unparseable arguments, the loop appended the
  assistant message with its `tool_calls` but skipped the tool response for
  that `tool_call_id`, then continued to another turn — so the next request
  carried an unanswered `tool_call_id` and providers rejected the run
  (`An assistant message with 'tool_calls' must be followed by tool messages
  responding to each 'tool_call_id'`). The same structural defect existed in
  the validator loop for `submit_verdict` with an invalid severity. Every
  `tool_call_id` in an assistant message now gets a tool response before the
  next request, without exception: terminal tools, failing tools, tools
  returning nothing, and turns mixing several calls. A hypothesis property
  test drives the loop with arbitrary tool-call turns and asserts the
  invariant on every request, so any future tool that can skip its response
  fails the test. (Fixes #53).

- `submit_files(0 file(s))` is no longer accepted as a valid completion.
  Submitting zero files is not a valid delivery of changes; the loop now
  refuses it with a tool response telling the coder to produce at least one
  file operation and call `submit_files(files=[...])` again, instead of
  terminating the run with an empty artifact and a broken history.

- In-place-writing coders (opencode and similar, which write to the working
  tree directly and never go through `WorkspaceMCP`) can no longer mutate
  `.snodo/` and have it silently absent from the artifact report and audit
  trail. Previously `.snodo/` entries were *filtered* out of the returned
  `CodeArtifact`, which removes the only evidence of a write that already
  happened — and since `.snodo/` is normally gitignored, the git readback
  could not see the mutation at all. The in-place adapters now inherit
  `InPlaceCoderAdapter`, which snapshots `.snodo/` around the coder call and
  raises `SnodoMutationError` if the coder changed anything under it; the
  engine surfaces this as a terminal `blocker` halt, records a
  `snodo_mutation_blocked` audit event naming the paths, and leaves the tree
  for operator inspection — a `.snodo/` mutation is a governance violation,
  not an execution fault. The `.snodo/` artifact filter is removed from the
  in-place adapters; in-process adapters (litellm, mock) are unchanged, as
  they can only write through `WorkspaceMCP`, which refuses `.snodo/` writes.
  See ADR 027. (Fixes #52).

- Guaranteed hermetic execution under `--mock`. `MockAdapter` now provides a
  hermetic `_completion_fn` (`mock_completion_fn`), `_build_completion_fn`
  preserves mock binding without attempting live provider API key loading, and
  global mock mode (`set_mock_mode`) ensures that wave classification, LLM
  validators, spec authoring rewriters, and any future call sites cannot make
  live provider calls or touch network credentials under `--mock`. (Fixes #12).

- The verification toolchain is now pinned to exact versions. `ruff` was
  declared `>=0.1.0` with no upper bound, so two worktrees of the same commit
  resolved different ruff versions and the lint gate reported 0 vs 1909 errors
  for identical code — the gate disagreed with itself. The tools every
  verification command invokes (`ruff`, `pytest`, `pytest-cov`,
  `pytest-timeout`, `pytest-xdist`, `hypothesis`, `import-linter`, `grimp`,
  `genbadge`) are now declared with exact `==` pins in both dev sections, and
  `uv.lock` carries the same specifiers. A golden test
  (`tests/golden/test_toolchain_pin.py`) fails if any of these tools is
  declared with a range, or if the lockfile resolves one at a different
  version, so an unbounded declaration cannot reappear unnoticed. Dependabot
  (pip, weekly) is unchanged: upgrades now arrive as dependabot PRs that move
  the exact pins together rather than floating on `>=`. (Fixes #50).

- Protected `.snodo/` from tool-surface mutation across `WorkspaceMCP`
  (`write_file`, `delete_file`, `create_directory`) and `GitMCP`
  (`stage_files`). Mutation attempts targeting `.snodo/` raise
  `PathValidationError` naming the path, while read operations remain permitted
  and internal snodo state writes continue operating unimpeded. Coder adapters
  filter out `.snodo/` paths from returned `CodeArtifact`s. See ADR 026.
  (Fixes #34).

- `snodo init` now auto-detects test commands from project marker files
  (`pyproject.toml`, `package.json`, `Cargo.toml`, `go.mod`, etc.) or prompts
  for one interactively if none is inferred, writing `tooling.test_command`
  explicitly into `protocol.yml`. A new `--test-command` (`-c`) flag enables
  explicit non-interactive configuration. (Fixes #33).

- The test suite no longer mutates the repository it runs in. The truncation
  execution test built a protocol graph without a fixture `project_root`, so
  the engine's executor created and checked out a `task/{id}/{slug}` branch in
  the suite's own working tree — leaving HEAD on a stale task branch after
  `pytest`, so the next commit landed there instead of the branch the agent
  was on. The test now runs against a throwaway git fixture repository, and a
  session-scoped guard in `tests/conftest.py` records the suite repo's HEAD and
  branch set at session start and fails the suite if any test changes either.
  (Fixes #48.)

- Audit log loading errors (`AuditError`) during dashboard data provider
  instantiation and `snodo cloud sync` are now caught gracefully. Corrupt
  audit logs no longer crash the TUI dashboard (which degrades by omitting
  audit event details) or `snodo cloud sync` (which reports the corrupt log
  per session and returns failure status). (Fixes #14).

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

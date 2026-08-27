# Changelog

All notable changes to snodo will be documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
snodo uses [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

### Added

- `snodo recon` LLM endpoint resolution and CLI worker completion fixed. `_call_agent` in `snodo.recon` now resolves `api_base` via `ConfigManager.resolve_api_base(model)` and binds provider `extra_headers` (such as Cloudflare `x-session-affinity`), preventing authentication failures when models route through custom endpoints. Additionally, CLI `recon_command` now waits for worker thread completion (`ReconManager.shutdown()`) and outputs the completed results directly to stdout, eliminating interpreter teardown crashes (`cannot schedule new futures after interpreter shutdown`). (Fixes #95).

- Pre-execute validator findings regarding existing repository state during recovery attempts (`task.depth > 0` or prior failures) are passed forward to the coder as non-blocking evidence instead of triggering pre-execute policy escalation or recovery deadlocks. Previously, when a pre-execute validator (such as `architecture` using read tools per ADR 019) detected pre-existing repository state left by an earlier attempt (e.g. a dead module or stale rule), its verdict triggered pre-execute policy escalation under unanimous policy before the coder ran — blocking the exact attempt intended to fix the tree and creating a permanent recovery deadlock. `run_validators` and `PolicyEvaluator` now convert non-error pre-execute recovery tree-state findings into non-blocking evidence passed to the coder, while operational errors (`error=True`) remain fail-closed. (Fixes #90).
- A task spec that cites a repository path the worktree cannot see is now
  surfaced before the coder is dispatched. A spec naming a file that exists in
  the operator's working tree but is untracked is absent from the task worktree
  (built from the branch), so the coder writes its own version of the file and
  the validators then judge the work against the document the coder just
  authored — every verdict internally consistent, nothing reported unusual.
  `snodo run` now warns when the spec cites paths that do not exist in the
  worktree, and `create_worktree` surfaces untracked files in the project root
  at worktree creation, making "the operator can see it and snodo cannot" a
  visible fact. This is a warning, not a halt: specs legitimately name paths
  that are meant to be created, and only the operator can tell the two apart.
  (Fixes #93).
- File deletion support in `submit_files` without requiring `content`. Extended `submit_files` tool schema (`content` optional for delete actions) and `FileArtifact` interface (`content: str = ""`), and updated coder prompt instructions so deleting obsolete or orphaned files created in previous recovery attempts is discoverable and executable without reading file contents first. Executor ignores `FileNotFoundError` when deleting non-existent paths to prevent execution crashes. (Fixes #91).

- `snodo merge` audit log resolution fixed for merges executed from repository roots. `_record_merge_and_review` now wraps audit log resolution and event appending safely inside a `try` block, initializing or loading `<repo_root>/.snodo/audit.log` when merging from a repository root (with or without a pre-existing `.snodo/` project directory), and recording both `task_merged` and `human_review_recorded` events. Loud degradation is preserved so that any unresolvable audit log failure emits an explicit warning rather than throwing an exception. (Fixes #88).

- `snodo merge` conflict messaging updated to accurately reflect that aborted merges leave the base branch clean, and now lists all conflicting file paths. `GitMCP.merge_branch` executes `git merge --abort` on conflict to preserve base branch cleanliness for automated workflows. Previously, the CLI output falsely claimed "The branch and worktree were left intact for manual resolution. Resolve it, then re-run `snodo merge` to continue." when the index was actually clean (`git ls-files -u` empty). The CLI now explicitly lists conflicting paths, explains that the merge was rolled back, and outputs the exact command (`git merge <branch>`) for manual resolution.

- `snodo merge` error messaging for staged index files updated and redundant checkouts skipped. Plain `git merge` strategy ort refuses when files in the index are staged during a 3-way merge attempt (`overwritten by merge`), raising exit code 128. `GitMCP.merge_branch` now detects staged changes in the index (`git diff --cached --name-only`) and reports explicit error advice asking the operator to unstage or commit them before merging. Additionally, redundant `git checkout <base>` calls when already on the base branch are skipped to avoid unnecessary index locks and enable cleaner execution.
- Structured output degrades instead of failing when a provider rejects
  `response_format`. DeepSeek returned 400 "This response_format type is
  unavailable now" for every validator call and the wave classifier,
  intermittently, for over an hour — a provider rejecting structured output
  took every validator down. When the structured call is rejected (a 4xx other
  than 429), the validator now falls back to an unstructured call and parses
  the verdict from the content; if the fallback does not parse, that is an
  operational fault (`error=True`), not a warn verdict. The transient-retry
  predicate is also honest now: it classifies on exception type and HTTP status
  code instead of substring-matching error prose (the old predicate matched
  `"500"`/`"502"`/`"deepseekexception"` anywhere in a message, so every error
  from that provider was retryable). A 4xx other than 429 is a client error and
  is not retried; 5xx, 429, connection, DNS and timeout errors still are — the
  retry that rescued this incident is kept. (Fixes #84).

- Transient LLM provider and network errors during validator execution are now retried before emitting an operational fault, and unrecoverable provider/tool-loop exceptions are reported as `error=True` (`validator_error`) rather than `warn`. Previously, a transient DNS resolution error (`[Errno 8] nodename nor servname provided, or not known`) or API timeout in `LLMValidator` returned `severity="warn"` (`error=False`), causing unanimous disagreement policies to mistake an infrastructure fault for a code judgment warning and trigger unnecessary recovery cycles on healthy runs. Retrying transient errors (up to 3 attempts) transparently resolves momentary network blips, while unhandled operational faults halt cleanly as `validator_error` without entering recovery loops. (Fixes #82).
- Parallel merges no longer conflict on `CHANGELOG.md`. Every agent appends its
  entry at the top of `### Added`, so any two branches collide on the same
  lines by construction — six agent merges produced six CHANGELOG-only
  conflicts, all resolved identically by keeping both entries. `CHANGELOG.md`
  is now marked `merge=union` in `.gitattributes`: git's built-in union driver
  merges a parallel append as "keep both", dedupes identical entries, and needs
  no per-machine configuration. Chosen over fragment files (`changelog.d/` +
  an assembly step), which would cost an assembly step and the single readable
  CHANGELOG in the working tree to fix a conflict a merge driver resolves
  correctly for free. A canary test
  (`tests/golden/test_changelog_union_merge.py`) fails at the branch if the
  `.gitattributes` declaration is dropped. See ADR 037. (Fixes #81).

- The tool-loop read memory now covers ranged reads and repeated directory
  listings, which is where transcript growth actually came from. The previous
  dedup keyed on exact tool arguments, so `read_file_lines(f:1-400)` and
  `read_file_lines(f:400-520)` were distinct calls and neither was served from
  memory — in one observed run the coder read a file in four ranged chunks and
  most test files in two, with the dedup never firing once, and re-listed
  directories it had listed forty turns earlier. `ReadMemoryTracker` now records
  the line ranges already fetched per file and serves any read contained within
  them from memory with a turn pointer, and does the same for repeated listings
  of the same canonical directory. Applies to both the coder and validator tool
  loops. (Fixes #77).

- Runbooks brought up to date and their central finding corrected. Runbook 02
  §9.1 previously claimed every defect was caught by `quality`, the only
  validator that executes something, and that read-only judges passed
  everything. That was true when written and is now too broad: pre-execute
  `architecture` has repeatedly rejected real defects before any code existed
  (an unsatisfiable acceptance criterion under the recorded Node floor citing
  ADR-0001; a spec asserting the dependency guard was unaffected when the
  accumulated failure showed it rejecting the new import; a stored card
  requiring a template id the schema lacked — the last corroborated in
  `docs/architecture/maturity-assessment-2026-08.md`), while post-execute
  judgement of artifacts has been weak (the `acceptance` canary rejects a real
  omission per Fixes #59, but a live run returned MET for a command it could
  not run while `quality` held that command's failing output — Fixes #75). The
  sharper claim is now stated with citations: pre-execute judgement of a
  proposal has repeatedly caught real defects; post-execute judgement of
  artifacts has been weak; execution is what catches those. What remains
  uncertain is stated explicitly. Both runbooks are refreshed against ADRs
  030–036 and the verification work (CI-workflow canary, merge-gate polling,
  stale-conclusion detection, declared coder interface, operator review
  tracking).

- A local-suite canary that validates every file under `.github/workflows/`.
  The gate every other gate depends on is the CI workflow file itself, and it
  had no canary: `ci.yml` was invalid YAML for several merges (the
  patch-coverage step embedded unindented Python in a `run: |` block scalar),
  every CI run failed at startup with no log and no test output, and nothing
  noticed because the merge path ran its gates locally. The check parses each
  workflow as YAML and asserts it is structurally a workflow (`on` trigger,
  non-empty `jobs`, each job with `runs-on` and at least one `uses`/`run`
  step), plus canaries proving the gate fails on a malformed workflow. Depth
  decision: parse + structural validation, no workflow-schema package — PyYAML
  is already a dependency and the structural checks catch the failure class
  that occurred; a full GitHub-Actions schema validator is a heavier
  dependency not warranted for files we author. (Fixes #74).

- A criterion naming a command the acceptance validator cannot run is now
  UNCHECKABLE by construction. A read-only judge without a shell was returning
  MET for "make check passes" — reasoning that nothing in the tree demonstrably
  failed it — while `quality`, which had actually run the command, held its
  failing output in the same cycle. "Verified from the tree" is a weaker claim
  than "the command passed", and conflating them let a judge assert something
  execution had already disproved. Post-execute validators additionally now
  detect that contradiction: when an execution validator reports a failure and a
  read-only validator claims the same command passed, the read-only verdict is
  superseded and a `validator_contradiction_detected` audit event records it.
  (Fixes #75).

- The CI merge gate (`snodo merge`) now polls instead of checking once. Right
  after a push GitHub has not registered the run yet, so an immediate "CI has
  never run" was a race, not a verdict — the first merge of every branch
  failed and the operator had to retry by hand. The gate now waits (with
  visible progress and a timeout) for a run to appear and conclude
  (`wait_for_ci_conclusion`), then decides. (Fixes #72).

- CI conclusions now carry their context and distinguish what actually
  happened. Every conclusion reports the run id, the commit it ran on, and
  when it concluded; a run whose commit is not the branch tip is `stale` and
  is never presented as the branch's verdict (a stale failed run was being
  quoted as current after a fix landed on main). `startup_failure` (a broken
  workflow — the operator's next action is fixing CI, not the branch),
  `cancelled` and `timed_out` are reported as distinct states instead of a
  generic "fix the failure". (Fixes #76, the failure-distinction half of #74).

- The merge gate is no longer run from an agent's worktree.
  `scripts/merge-agents.sh` invoked `uv run --project ~/Dev/snodo-a snodo`,
  so the version of snodo enforcing the gate depended on what an agent
  happened to have checked out — possibly mid-task or about to be reset. The
  editable-checkout fallback now resolves against the repository being merged
  (`$PWD`), and an installed `snodo` is preferred. (Fixes #73).

- Real-time terminal progress visibility for post-execute validator verdicts and recovery transitions. Post-execute validator warnings, blockers, and errors are surfaced as they land with icons (`⚠️`, `❌`, `💥`) and clean first-line justification snippets. Recovery subtask spawns (`Recovery (attempt N/M): spawned <fix_task_id> (...)`), recovery stalls (`Recovery stalled`), and depth exhaustion (`Recovery depth exhausted`) are explicitly printed during execution. (Fixes #71).

- Operator human review tracking (`snodo task review <task_id> <verdict>`) and acceptance rate reporting (`snodo task report` / `snodo task review --report`). Reviews append `human_review_recorded` events to `.snodo/audit.log`, maintaining hash-chain integrity. Reports calculate the fraction of completed tasks accepted unchanged over a rolling window (3-category taxonomy: `accepted`, `amended`, `discarded`). Machine-readable JSON output (`snodo.task_review_report.v1`) included. See ADR 036. (Fixes #70).

- ADR numbering conformance gate. Two agents working in parallel have claimed
  the same ADR number three times (023, 028, a 030/031 near miss), each time
  surfacing as a merge conflict in `docs/decisions/README.md` plus a renamed
  file whose heading still carried the old number. A new test
  (`tests/golden/test_adr_index.py`) fails at the branch if an ADR file's
  number does not match its heading, if any number appears more than once in
  the index, or if the index and the files on disk disagree. It caught a real
  stale link (ADR 001). Sequential numbering is kept — collision-resistant
  allocation needs a shared serial allocator across agents, which is heavier
  than the mechanical renumber the gate makes trivial. (Fixes #55).

- A load-time warning for a unanimous policy with exactly one `post_execute`
  validator. `PolicyEvaluator` derives `total_count` per phase, so under
  `"unanimous"` a single post-execute validator is an unopposed veto over
  completed work — and with `quality` and `acceptance` both post-execute, an
  operator choosing the policy should meet this before it lands in a halt
  payload. The verifier (WF4) now warns when a protocol has exactly one
  post-execute validator under unanimous; the warning is surfaced by
  `load_protocol` and `snodo init`, and the policy section of
  `docs/protocol.md` plus the `solo` template explain the tradeoff. (Fixes #41).

- Documented running snodo from an editable checkout in `CONTRIBUTING.md`.
  `snodo` is not on PATH outside its own checkout, and `uv tool install snodo`
  pulls the five sub-packages from PyPI (non-editable), which defeats the
  point when patching the engine. The alias
  `snodo='uv run --project ~/path/to/snodo snodo'` is now the documented way to
  develop against the source tree. (Fixes #45).

- The opencode coder path is now explicitly **experimental**, not supported.
  The containerised `opencode` and host `opencode-cli` backends are exercised
  by the adapter conformance suite and the `.snodo/` guard and commit hold for
  them (ADR 027/030), but they do not yet report per-turn progress or
  contribute usage/cost records, and no shipped template uses them. The
  position is recorded where an operator meets the path: `docs/protocol.md`
  (the `coder` field and a "Coder backends" section), `docs/architecture.md`,
  the `snodo init` Docker check output, and the minimal-webapp runbook. See
  ADR 034.

- Cost attribution is declared **operational telemetry, not part of the
  attestation**. The audit trail (INV4, ADR 031) records what a run decided
  and whether verification ran; it never carries cost — for any coder. Token
  and cost data live in per-job `state.json` (`snodo meta`), and even the
  supported `litellm` path records them only for background jobs. The opencode
  paths' absent usage/cost records are therefore a documented non-goal, not a
  gap in the attestation. Whether cost should ever become attestable is a
  change to the attestation contract for all coders (issue #69), deliberately
  out of scope. See ADR 034.

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

- The merge gate no longer serialises on CI. `snodo merge` previously pushed
  and polled one branch at a time, so merging N branches cost N CI runs in
  series — measured today at five branches and roughly forty minutes for a
  suite that runs in under a minute locally. It now pushes every branch in
  scope up front (so the CI runs overlap) and then polls+merges each, cutting
  the wait to ~one run. A branch whose local and remote have diverged — the
  normal case after a branch was recreated from main following a reset — is
  force-pushed with `--force-with-lease` instead of failing a fast-forward
  push (this blocked two merge runs today). A new `snodo ci-wait <branch>`
  command gates the MERGED result: after the merge is pushed, the base
  branch's own CI run is the gate on the combined result, because per-branch
  CI cannot catch two branches that pass alone and break together (two
  branches editing the same CI step did exactly that today). The wrapper
  (`scripts/merge-agents.sh`) now relies on `snodo merge` for the push and
  runs `snodo ci-wait main` after pushing, refusing to reset worktrees onto a
  red combined result. (Fixes #92).

- CI's test job now runs the full suite in parallel (`-n auto`). The job
  previously ran `pytest tests/ -m ""` serially — the full suite including e2e
  took 385s (run 33085073065) and 369s (run 33102852491) while the same suite
  local is ~50s with `-n auto` and e2e deselected by default. Per-step timing
  showed the time was not in dependency setup (`uv sync` ≈ 5s) or uv install
  (≈ 3s) but in the Test + coverage step itself (238–356s): it runs the entire
  suite — e2e included — on a single process. Adding `-n auto` parallelizes
  across the runner's cores; nothing tested is dropped (the full `-m ""` suite
  still runs, and the e2e suite still runs in CI). Local reproduction of the
  exact CI command with `-n auto`: 138s vs 385s serial, coverage gate still
  passes at 71%. (Fixes #89).

- Recording a review outcome is now part of the merge, not a separate act of
  discipline. `snodo task review <id> accepted|amended|discarded` shipped with
  ADR 036 and was never run once — a measurement that depends on remembering
  does not get taken. `snodo merge` now records the verdict at the moment the
  operator looks at the work and decides: each merged branch is written to the
  audit log (`task_merged` + `human_review_recorded`), with the verdict taken
  from `--review <verdict>` when given, prompted interactively on a TTY, or —
  when the merge is unattended (`--no-review`, no TTY, or no answer) —
  recorded as **unreviewed**. An unreviewed merge is never silently counted as
  accepted: the report's rate is computed over reviewed tasks only, and the
  unreviewed count is explicit. (Fixes #83).

- Recovery specs no longer widen the coder's sense of scope. The recovery
  spec previously opened with "Fix the following failures... resolve all of
  them" and "Address every failure listed below", making the accumulated
  failure list the operative instruction — and since failures accumulate with
  every attempt, the coder's scope grew with each cycle. Observed twice in
  real runs: a first attempt reached `submit_files` at turn 16 with 11 files,
  while its recovery read essentially the entire repository across 48 turns
  and 18 minutes before submitting 4 files. The intent is now the operative
  instruction ("The task is the INTENT below. Implement it.") and the failures
  are explicitly framed as diagnostic evidence that does not change the task
  or widen its scope. The intent is still carried exactly once, unchanged, and
  the failure evidence is still preserved verbatim (ADR 021). (Fixes #78).
- `WorkspaceMCP` now refuses access to `.git/` paths at the tool surface.
  Exact `.git`, paths under `.git/`, and absolute `.git` paths raise
  `PathValidationError`, and directory listings omit `.git` entries while
  `.gitignore` and normal project files remain accessible. (Fixes #80).

- The recon test fixture no longer races the background worker. `recon_mgr`
  isolates `snodo.recon._threads` and stubs the worker body before assertions,
  so `status="running"` reflects the state written by `submit()` rather than a
  background completion that happened to win the race. A deterministic test now
  covers that `submit()` still starts a background worker. (Fixes #86).

- E2E CLI fixtures now nest the project root under each test's tmp path and
  expose the isolated `snodo_home` separately. This keeps `snodo run` worktree
  siblings (`<project_root>/../.snodo-worktrees`) per-test instead of sharing
  one worker-level sibling directory, hardening the e2e suite for CI's parallel
  `-n auto` run. (Fixes #94).

- `snodo merge` no longer blames a `startup_failure` CI conclusion on the
  branch. A run that never started — typically an invalid workflow definition
  (bad YAML, malformed step) — is not the branch's fault; the message now
  names the workflow-definition problem and points at `.github/workflows/`,
  which the local suite validates. (Fixes #74).

- Merges are now authorised by each branch's CI conclusion, not by an agent's
  self-reported gate results. The real merge path (`scripts/merge-agents.sh`)
  now delegates the merge to `snodo merge`, the CI-authorized merge engine: it
  first pushes each agent branch to origin so the CI workflow (`push:
  branches: ['**']`) runs on it, then calls `snodo merge` which queries
  `gh run list --branch` and refuses any branch whose CI has not run, is in
  progress, or has failed. `snodo merge` now operates on the git root (not a
  `.snodo/` project), accepts multiple branches per invocation plus the agent
  short names (`a`, `snodo-a`), skips branches with no new commits
  (resume-safe after a hand-resolved conflict), and stops on the first
  refusal or conflict. The wrapper keeps the environment-specific guards the
  tool cannot know about: it must run on `main`, the merged result is pushed
  before any worktree is reset, and a worktree is reset only when its branch
  is provably on `origin/main` AND the worktree is clean. A merge still never
  depends on a PR existing. (Fixes #57).

- Updated runbooks (`docs/runbooks/01-minimal-webapp.md` and `docs/runbooks/02-greenfield-protocol.md`).
  Drafted missing Section 9 Result in runbook 01, and brought runbook 02 up to date to reflect fifty closed
  issues and ADRs 015–034. Synthesized empirical findings across runbook executions: execution (`quality`)
  remains the primary authority for code correctness over read-only LLM judges; early defect patterns
  tended toward safety properties degrading to warnings or operational errors reported as judgements;
  and recent verification hardening (audit events, patch coverage, canary gates) eliminates silent green gates.

- The coder-adapter capability surface is now a DECLARED interface instead of
  `hasattr` duck typing. The engine previously reached into adapters behind
  guards (`if hasattr(coder, "progress_callback"): coder.progress_callback =
  ...`), so any capability it offered optionally was one some adapter silently
  lacked — which is how per-turn progress landed on one adapter and went
  unnoticed on the opencode adapters for weeks. The optional capabilities
  (`workspace_mcp`, `progress_callback`, `_job_id`, `_task_id`, `model`,
  `skip_workspace_write`, `skip_engine_commit`) now have base-class defaults on
  the `Coder` ABC, the engine assigns them unconditionally, and a conformance
  test asserts every registered adapter carries them — "this adapter does not
  support X" is a visible fact, not a silently skipped line. See ADR 035.
  (Fixes #68).

- "Coder produced nothing" now raises `ExecutionError` on every adapter path.
  It was a hard fault on litellm but downgraded to an audit note whenever
  `skip_engine_commit` was set, so a no-op opencode run continued quietly. The
  principle: opting out of a mechanism must not silently discharge the
  responsibility that mechanism carried — `skip_engine_commit` controls who
  commits, not whether observable work was produced. The
  `empty_artifact_warning` audit note is removed. See ADR 035. (Fixes #68).

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

- The halt payload printed on a successful closure now shows the resolving
  attempt's verdicts, not the first attempt's. A task that resolved through
  recovery previously printed `"status": "completed"` alongside
  `validator_results` containing the FIRST attempt's warns, `"iteration": 1`,
  and `"post_validation": {"outcome": "recovery"}` — the verdicts belonged to
  the attempt that failed, not the one that resolved, so a reader saw two
  warns and an escalate policy decision under a completed status and concluded
  the run failed. The root's graph invocation ends at the `recovery` node,
  which writes a payload with `final_decision: "completed"` but `phase:
  "unknown"` and the first attempt's results; the genuine completion lives in
  the resolving subtask's payload (`phase: "complete"`). The terminal-payload
  selection now prefers the deepest genuine-completion payload over the root's
  recovery-node payload when the tree resolved. (Fixes #85).

- Full-suite runs no longer pollute the suite repository's own `.snodo/`
  directory. The verification-audit work (#60) made `QualityValidator` record
  `verification_executed` events through a cwd-relative `get_audit_log()`
  (default `.snodo/audit.log`). The code under test is not always the project
  under test: tests that dispatch the quality validator run with the process
  cwd at the suite repo, so under `pytest -n auto` concurrent workers appended
  to the same repo-root audit file, corrupted the hash chain, and the next run
  failed two tests in `test_run_cmd.py` on a sequence discontinuity. The
  validator now takes the audit log ONLY from the validator context — a
  verification event without an explicit audit log in context is skipped,
  never resolved from cwd. The session-scoped conftest guard (in the spirit of
  the #48 guard) now also fingerprints the suite repo's own `.snodo/` and
  fails if any test writes under it; it immediately caught a second instance of
  the same class — a hermeticity test built a graph without a `project_root`,
  so wave classification wrote `.snodo/wave.json` into the suite repo — now
  fixed to run against an isolated fixture repo. (Fixes #65).

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

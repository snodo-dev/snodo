# Changelog

All notable changes to snodo will be documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
snodo uses [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

### Fixed
- Liveness pushes now share the audit sender's persisted terminal-refusal state,
  stopping repeated authentication failures across transitions and restarts
  while leaving rate limits and server failures retryable. (Fixes #374)
- Issue-closing commits are now required to document their changes in the
  pending changelog section instead of a dated release section. (Fixes #372)
- Completed-task audit events now preserve the final task-branch commit SHA, or
  explicit null provenance when the task committed nothing. (Fixes #373)
- Cloud liveness and audit sync now share bounded exponential backoff for
  transient cloud failures, honoring server delays and adding jitter so
  clients do not retry in lockstep. Authentication failures remain terminal.
  (Fixes #375)
- A send rejection now discards the cached lease and permits one replacement
  exchange; a second rejection persists the refusal and stops future pushes.
  (Fixes #378)

### Added
- Benchmark plan runs can now use an external clean Git fixture, cloning it into
  a disposable execution directory and reporting its committed-tree identity so
  repeated runs begin from the same state. (Fixes #379)

- Completed task records now include a nullable effort cost containing measured
  tokens, turns, attempts, runtime, and change size, with Snodo, protocol,
  fixture, coder, and model provenance. Missing measurements remain null rather
  than being conflated with measured zero values. (Fixes #380)
- A completed task now records how much it changed, not just which files it
  touched: `task_complete` carries a `change_size` of line totals and per-shape
  file counts, computed against the merge-base the task branched from — never
  the base branch's later tip — so a typo fix and a subsystem rewrite are
  distinguishable and other people's merged work is never attributed to the
  task. Changes whose lines are not countable (binary, rename, mode-only)
  report their own counters instead of a zero, and a change past
  `CHANGE_SIZE_MAX_FILES` reports `lines_added: null` with `capped: true`
  rather than stalling the run on the comparison. The diff itself is never
  recorded. The field rides the published engine-to-cloud interface, whose
  version moves to 2. (Fixes #377)

- Cloud-bound senders (`cloud_sync` and `cloud_liveness`) now exchange the API
  key once for a short-lived lease before sending, addressing paths containing the
  lease identifier with the opaque bearer token instead of presenting the API key
  directly to fixed endpoints. (Fixes #376)

- Completion records now carry the model the provider said it served, beside
  the model that was asked for. Per-turn coder and validator telemetry gained
  `model`/`served_model`, usage records gained `served_model`, and recon
  results gained a `served_model` field, so a model substituted behind a
  stable name is visible in the record instead of only inferable from weeks
  of behaviour. Requested-equals-served is recorded too — it is the evidence
  that nothing changed — and a provider that reports nothing records `null`,
  never an assumed match. Provenance only: the field never feeds routing,
  selection or failover, and a mismatch raises no warning. (Fixes #381)

### Changed
- The task start and completion recording path moved whole from
  `snodo/cli/commands/run_cmd.py` into `snodo/cli/commands/task_record.py`,
  bringing run_cmd back under its file-length baseline (it had grown 64 lines
  past it, blocking unrelated merges at the gate). A pure move: the recorded
  bytes, the order of operations and every import path are unchanged —
  `run_cmd` re-exports both functions. (Fixes #382)

## [0.12.1] — 2026-09-19

### Fixed

- Model benchmarks now pass the credential resolved for their selected model on
  each completion call, so configured providers work without shell credentials.
  (Fixes #370)
- `snodo models --check` no longer sends subprocess-coder model namespaces to
  LiteLLM, which previously reported working coders as provider failures. The
  check now reports these models as not checkable by the provider canary and
  separately identifies missing coder binaries. (Fixes #371)
## [0.12.0] — 2026-09-19

### Fixed
- The published cloud interface schema ratchet is now invoked in CI and release
  workflows. The check was merged to guard against silent payload shape changes
  without a version bump, but was previously omitted from both workflow files.
  (Fixes #365)



- LiteLLM completions now receive each model's API key per call instead of
  sharing provider environment variables, so routed custom providers remain
  isolated during concurrent recon and leave no credential behind. (Fixes #369)

- An interactive plan watch now appends a refreshed task tree when a task
  changes status, so the opening snapshot is not mistaken for the current
  state. Quiet watches still use one heartbeat row, and redirected output is
  unchanged. (Fixes #368)
- The extension guide's "Coder adapters" section showed `Coder` as a single
  abstract method and cited a line range that no longer contains the class.
  It now documents the full attribute contract — the workspace, progress
  sink, correlation ids, and the two commit/write behavioural switches the
  engine sets on every adapter instance unconditionally, with the default
  each gets when an adapter does not override it — and points at the
  class's current location. (Fixes #366)

### Added
- `snodo models --check` makes one cheap live request against each distinct
  model configured for the coder, validators, classifier and recon, then
  reports each model as healthy or names the provider refusal, unknown model,
  or other error. It is an operator instrument, not a dispatch gate, and does
  not run the engine loop. (Fixes #362)
- Plan validation now warns when two task specifications in the same wave cite
  the same file, naming both tasks and the possible overlap. The advisory is
  based on plan-time citations and does not block validation or dispatch.
  (Fixes #363)
- The CI gate now runs the existing strict documentation build, so broken
  internal links and missing navigation pages fail before deployment. (Fixes #364)

- `snodo models --benchmark --json` now emits a versioned, machine-readable
  result containing prompt and model provenance, every attempted sample,
  successful and attempted run counts, and successful-run aggregates. The
  listing and usage-statistics forms have separate JSON schemas. (Fixes #367)

## [0.11.0] — 2026-09-19

### Added

- Issue-closing commits are now checked against the branch range before they
  land. The gate names an undocumented issue and asks its author to explain the
  change, without rechecking older history or generating release notes from a
  commit subject. (Fixes #361)
- `snodo models --benchmark` can repeat its measurement. A single sample could
  not answer the question the flag exists to answer: four consecutive runs
  against one model gave first-token times of 2.71s, 1.51s, 0.98s and 1.65s,
  so any comparison drawn from one of them was drawn from noise. The command
  now reports median and mean for first-token latency and decode rate, says
  how many runs succeeded out of how many were attempted, and states the run
  count in the confirmation line before billing the operator for all of them.
  Output lengths are not averaged: they vary per run, so wall-clock totals are
  not commensurable and only the rates are. Runs are sequential — concurrent
  requests would measure the provider's concurrency, not its latency — and a
  failed run is reported rather than retried, because a silent retry would
  hide the provider flakiness the flag is used to detect. Default behaviour is
  unchanged: with no run count, one call, printed as before. (Fixes #346)
- A model can be selected by exact id. Where a model's id is a prefix of other
  ids, no substring selects it alone, so the advice to narrow the selection
  with `--id-contains` named a filter that could not express the request —
  `gemini-3.1-flash-lite` matched three models and the intended one was the
  shortest. An exact name matches one model or none, and matching none says
  so rather than falling back to substring matching and benchmarking a model
  the operator did not choose. (Fixes #347)
- Validators that judge only the specification now run when a plan is
  validated, so a verdict computable from the spec text arrives before any
  dispatch. One task failed three times on a conventions verdict about how its
  spec was written — a judgement needing no repository access at all — and
  three coder cycles were spent learning it. A validator is eligible only if it
  declares `judges_spec` and reads no repository tools, so a repo-dependent
  judge is never run against a repository it cannot see. Plan-time verdicts are
  advisory: they do not block, they do not dispatch, and they are never cached
  for reuse in the loop, whose quorum is unchanged. (Fixes #350)
- A validator can declare what it judges: the task, or the wave the task
  belongs to. A judge asking whether a ticket is too large is asking about that
  ticket; a judge asking whether a design is coherent is asking about a body of
  work, and a wave is that body. Given the same subject the two could only
  disagree, and they did. The declaration defaults to the task, so every
  existing protocol keeps its behaviour with no edit. This is a property of a
  validator in a protocol — no engine state, severity, halt type or task status
  was added. (Fixes #356)
- A wave-scoped judge is keyed on the wave's specifications rather than on one
  task's, so two sibling tasks ask the same question under the same key and the
  verdict cache serves the second from the first. The structural rules are
  unchanged: a judge that reads the repository is still keyed on the tree, and
  every post-execute judge still is, because a judgement about produced work is
  a judgement about the work. (Fixes #357)
- A wave-scoped judge is now evaluated once, as the wave is entered, and its
  verdict carried by every task in that wave. Running it per task meant running
  it once per sibling, and where siblings run concurrently a shared cache does
  not help — all of them start together, all of them miss, all of them compute
  the same verdict. Moving when the judgement happens removes the duplication
  rather than coordinating it, so no lock or single-flight guard was needed.
  The in-loop quorum is unchanged: no task skips validation because a wave
  verdict existed. (Fixes #358)
- Findings are a first-class deliverable. An investigation or survey task
  produces no diff, and every path through the engine assumed a task ends in
  one — so an agent asked for a written survey produced it, found nowhere to
  put it, and reasoned its way to an empty commit which the gate then verified.
  The findings existed only in that agent's narration, and a task that touched
  nothing looked exactly like a task that did nothing. Findings now travel on
  the coder contract, surface in writeback and halt payloads, and persist to the
  task record, so an operator can read them without going back through a
  transcript. No state, severity, halt type or task status was added.
  (Fixes #359)

### Fixed


- A validator's repeated reads no longer spend its turn budget. The tool loop
  already intercepts a read identical to one from an earlier turn and points
  the judge back at it instead of re-reading — the response even says
  repeating "wastes a turn" — but the turn was charged against
  `max_tool_turns` anyway, at exactly the moment the judge learned nothing.
  On a real project a validator ran to turn 40 without ever reaching a
  verdict; the cap had already been raised once, from 12 to 40, for the same
  failure, and raising it again would not have helped, because a judge that
  circles will circle through any budget. A turn whose tool calls are all
  repeat-read hits is now free; a separate, small streak counter still stops
  a judge that only ever repeats itself, sooner than the real budget rather
  than looping through it, and it still either delivers a real verdict or
  fails closed — no new severity, halt type, or task status. (Fixes #360,
  ADR 050)
- Validators recover from a provider that refuses a parameter. `drop_params`
  removes only what litellm knows is unsupported, and for a model whose entry
  carries no supported-parameter list it removes nothing — so the request went
  out whole and was refused whole, and the validator reported an operational
  error instead of a judgement. This is not a gap that closes with time: the
  models worth trying are the new ones, and those are exactly the ones whose
  entries are unfilled. A refusal that names a parameter now sends the request
  again without it, following the pattern the tool loop already used for
  `tool_choice`. Deterministic judging is unchanged where the model supports
  it, and a refusal that names nothing still arrives as `validator_error`.
  (Fixes #345)
- The wave classifier no longer fails on every run. It sent `temperature` on
  every call, and where the model refused it the classifier tried twice, gave
  up and left the task unwaved — on every run of an overnight session, never
  blocking anything, which is the kind of noise that teaches an operator to
  stop reading stderr. Two provider refusals were seen: one rejecting the
  value ("does not support 0.0 ... only the default (1)"), one rejecting the
  parameter outright. Clamping answers the first and not the second, so the
  classifier now retries without the refused parameter, and a removed
  parameter stays removed on the following attempt. An unwaved task remains a
  non-fatal outcome. (Fixes #348)
- A validator that cannot be forced to commit is now told to. The final turn
  already narrows the offered tools to `submit_verdict` and requires it via
  `tool_choice`; when a provider refused that parameter the fallback deleted
  it and retried unforced, which on the final turn removed the only thing
  compelling a verdict. A judge then explored to its turn cap and halted
  having judged nothing, twice, at about fifty-five minutes each. The
  requirement now travels in the prompt when the provider cannot enforce it as
  a parameter. A judge that still returns nothing remains fail-closed and
  arrives as `validator_error`: no halt type, severity or status was added to
  hold a missing verdict. (Fixes #349)
- The protocol adherence validator recovers from a refused parameter. It sent
  temperature directly on both its completion calls, bypassing the wrapper that
  retries without a parameter the provider names — so it had none of the
  recovery the other validators had gained, and the same refusal had by then
  been fixed in three other places without this one being noticed.
  (Fixes #351)
- A task spec citing paths that do not exist is caught when the plan is
  validated rather than as the task goes out. The coder cannot see a file it
  was told to change, so it invents one and the validators then judge the
  invention as though it were the work asked for. The warning said exactly that
  and arrived after the ticket was already committed. In a monorepo the natural
  way to write a path is relative to its package, which resolved to nothing
  from the repository root; such a citation is now resolved against the
  repository's workspace roots before being called missing. The dispatch-time
  warning remains — the worktree can differ from the plan's view for reasons a
  plan check cannot see. (Fixes #352)
- A stale git index lock is diagnosed as itself. When a merge failed because the
  lock file could not be created, snodo reported git's error and moved on; the
  next merge failed the same way, for hours, blocking every dependent wave while
  the work was finished and verified. Where nothing is holding the lock, that is
  now named as the diagnosis — this is not a merge that failed, it is a machine
  that cannot merge anything — and the command that clears it is printed. The
  lock is never removed automatically: an operator removing one is making a
  judgement about their own machine, and a program doing it silently can destroy
  a concurrent operation's work. (Fixes #353)
- A wave blocked by unmerged work no longer reads identically to one blocked by
  unfinished work. Both reported "blocked (depends on: N)", though the first
  needs someone to look at merges and the second needs someone to look at tasks.
  The dependency wave's task statuses now decide which situation is named, and
  the tasks waiting to merge are listed when that is what is blocking. No status
  value was added; the information was already in the task records. (Fixes #354)
- The inert-settings notice is emitted once per configuration rather than once
  per dispatch. Telling an operator that the selected coder cannot honour a
  setting is useful; telling them twelve times during one plan run is how they
  learn to skip that shape of line. The message is unchanged and still names
  the setting and the coder. (Fixes #355)

## [0.10.3] — 2026-09-18

### Added

- A complete CLI command reference, covering every command and its options
  with worked examples. The surface had grown faster than the pages
  describing it; the reference cut the documentation-coverage baseline from
  53 undocumented surfaces to 23 in one commit. (Fixes #332)
- The liveness snapshot payload now has a declared type at every level —
  the snapshot itself and the plan, wave, task and job shapes nested beneath
  it. The format previously existed only as the code that assembled it and,
  in another repository, the code that read it: two independent
  implementations of something nobody had written down. Nothing on the wire
  changed. (Fixes #335)
- The audit ingest batch and its event envelope now have declared types, on
  the same terms and for the same reason. Values drawn from the engine's
  closed vocabularies are constrained to those vocabularies, read from the
  source `scripts/enforce_vocabularies.py` already derives them from rather
  than a second transcribed list. Nothing on the wire changed. (Fixes #336)
- `snodo cloud schema --json` publishes the OSS-to-cloud contract. Everything
  the engine sends a cloud backend goes through two calls — a PUT carrying the
  session snapshot and a POST carrying a batch of audit events — and that is
  the whole interface. It is now emitted as JSON Schema, generated from the
  declared types rather than transcribed beside them, so an integrator runs
  the command against the engine version they are targeting and gets that
  version's truth. The interface carries a version a consumer can use to
  decide whether it understands a payload. (Fixes #338)
- The published schema is now gated. A change to either payload's shape that
  does not match the recorded schema fails the build and names what moved,
  because once the interface is published a shape change is a change to
  somebody else's software and must be a deliberate version bump rather than
  an ordinary edit. (Fixes #337)

### Fixed

- Long-running `snodo serve --tunnel` child process pipes are now continuously
  drained in background threads. Previously, both the MCP server and
  cloudflared were spawned with pipes that were never read after startup,
  causing children to block inside `write()` once the OS pipe buffer filled up
  after hours of traffic. cloudflared stdout is now routed to devnull, and
  stderr streams are continuously drained while preserving diagnostic output on
  startup failures and unexpected exits. (Fixes #333)
- A cloudflared exit no longer orphans the MCP child. The tunnel's wait loop
  treated the tunnel process ending as a normal finish: it returned success
  without stopping the server it had started, and because that child sits in
  its own session nothing else took it down either. It stayed alive holding
  the port, and the next `snodo serve --tunnel` died on a raw EADDRINUSE with
  nothing naming the holder. (Fixes #334)
- Validators no longer fail on models that refuse a parameter they send. Every
  validator passed `temperature=0.0`, and a model that does not accept it
  rejects the whole request — so the validator reported an operational error
  instead of a judgement and the task could not proceed on that model at all.
  A hardcoded exclusion for one model family had been standing in for a
  general answer, and needed editing each time a provider shipped a model, by
  which point someone's run had already failed. The coder path had solved this
  generically; the validator path now does the same. Deterministic judging is
  unchanged where the model supports it, and a request the provider refuses
  still arrives as `validator_error`, never as a verdict about the spec.
  (Fixes #339)
- The validator retry now waits long enough to matter. Transient provider
  faults — 5xx, 429, connection, DNS, timeout — were retried three times with
  50 and then 100 milliseconds between attempts, so all three landed inside
  about a sixth of a second. The code read as resilient and behaved as if
  there were no retry at all; a 429 was answered with three requests in a
  tenth of a second, which is the behaviour a rate limit exists to stop. A
  provider-supplied retry delay is now honoured, the total time spent retrying
  stays bounded, and an exhausted retry still surfaces as `validator_error`.
  (Fixes #340)
- `docs/machine-interface.md` said JSON errors are written to stderr and never
  to stdout. The error emitter writes them to stdout, and says so in its own
  docstring. The page now describes what the code does, keeping the guarantee
  it was reaching for — that stdout is always one parseable document — which
  is true and is the reason the error goes there. (Fixes #341)
- `docs/authoring-a-plan.md` listed four plan task statuses where the runner
  writes five. `errored` was missing, along with the reason it is distinct
  from `blocked`: a blocked task is retried with its failure as context and an
  errored one is not, because handing an operational fault to a faultless
  coder as a critique is how a sound specification gets rewritten to chase a
  problem it did not cause. No status value was added; the page now documents
  the one that already existed. (Fixes #342)
- `docs/runbook.md` claimed every configuration key is settable without
  hand-editing. The setter accepts `model`, anything under `engine.` and
  anything under `llm.`, and answers everything else with an unknown-key
  error — the `cloud.` keys among them. The page now says which keys are
  settable and how the rest are changed. (Fixes #344)
- The greenfield runbook said `snodo init` commits `.snodo/`. It writes
  `.snodo/`, adds it to `.gitignore`, and commits only the `.gitignore` —
  deliberately, so a `git clean -fd` cannot remove the ignore file and thereby
  expose `.snodo/` to the next clean. A reader who believed otherwise would
  expect project state to travel with a clone. (Fixes #343)

## [0.10.2] — 2026-09-17

### Fixed

- A protocol can declare `protected_paths`, and a task whose branch diff
  touches one is blocked with the path named. Some paths in a repository are
  decisions rather than code, and until now the only defence was a sentence in
  a constraint — an instruction, not a boundary — so a coder implementing a
  feature could rewrite the decision record that governed it and nothing
  noticed. Detection reads the task branch's own diff, so it holds for every
  coder on every platform without privileges or mounts, and the work is
  unmerged when the check runs. Prevention (hiding a path from the task's
  worktree, read-only mounts in the container) is recorded in ADR 049 as what
  comes next, together with the platform truth that a same-user host
  subprocess cannot be given a real read-only boundary. No state, severity,
  halt type or task status was added. (Fixes #329)
- The liveness snapshot now carries durable wave identifiers, so a consumer
  can join it to ingested history. The snapshot nested tasks under a
  plan-local ordinal while history knew the same tasks under the registry's
  `w_xxxx` identifier, and nothing related the two — a completed wave could
  only render as a count. An enumerated task now carries its own identifier,
  and a settled wave reports the identifiers its tasks held alongside its
  counts, so a collapsed wave joins without being enumerated. The plan ordinal
  and the registry grouping remain different things and are documented as
  such. (Fixes #331)

- A failed recon now carries its reason to whoever asks. `get_status` loaded
  results only for `complete`, and `get_results` raised for any other status,
  so a recon that recorded `failed` and wrote why into `results.json` answered
  every caller with a bare `failed` — or an error — while the reason sat on
  disk. A terminal recon, failed or complete, now reports the results it
  recorded through both calls; a running recon is unchanged and still reports
  nothing. No state, severity or task status was added. (Fixes #327)
- A recon caller that names no agent now gets the configured recon models.
  The `recon` tool's `agents` property carried a JSON Schema default of
  `["default"]`, which many MCP clients materialise into the arguments they
  send — so a caller that never chose an agent arrived having named one, and a
  named agent short-circuits `llm.recon.models` entirely. The property no
  longer declares a default, so the wire can express the difference between
  "I named an agent" and "I named none"; an explicit `agents` list still means
  exactly what it names. Whatever `default` resolves to must also be callable:
  a project whose coder is a subprocess CLI can carry that adapter's namespace
  in its default model (`opencode-cli/...`, `agy/...`), a string the adapter
  strips before invoking its binary and which litellm rejects outright — recon
  now strips such a prefix to the provider model and names the substitution on
  stderr, falling back to the built-in default if nothing callable remains.
  No state, severity, halt type or task status was added, and failover within
  a chain, the fan-out, and what recon does once it has its models are
  unchanged. (Fixes #328)

## [0.10.1] — 2026-09-17

### Added

- The liveness snapshot now carries `last_activity_at`: when something last
  happened in the session across everything the snapshot reports — plan and
  task status writes included, not only audit events. A status write appends
  no audit event, so `last_event` (unchanged in meaning: the last recorded
  decision) could stay pinned hours behind the very change that fired the
  push, and a consumer rendering it as last activity saw a working machine as
  idle. `last_activity_at` is the newest of the record files' modification
  times and the audit tail's own timestamp — no new state, no audit event
  appended, no trigger or throttle change. (Fixes #324)
### Changed

- Liveness is now heard from even when nothing changes. The push was
  change-driven and throttled to at most one snapshot per session per
  interval, so a session that was running but quiet — a coder reading for
  twenty-four minutes without a status change — sent nothing, which is
  indistinguishable on the far side from a machine that has died. The
  interval is now a floor as well as a ceiling: at most one push per
  interval, and at least one while something is running. The reason the
  change-driven design existed is preserved — a session with nothing running
  still sends nothing, and a repeat push carries the same true snapshot
  rather than inventing state. The interval is configurable with
  `cloud.liveness_interval_seconds` (default 60 seconds, unchanged), so a
  deployment can lower it knowing the cost. Everything else about the wire
  is unchanged: full-snapshot PUT, drop on failure with no retry or queue,
  terminal transitions bypassing the throttle, and the sync/api-key gate.
  (Fixes #323)

### Fixed

- A task recorded `unmerged` whose branch an operator merged by hand is no
  longer re-executed on a rerun. `unmerged` is a claim about the tree, and the
  tree moves underneath it: after a manual merge the plan kept saying unmerged,
  so the wave dispatched a coder against a tree that already held the work and
  built its next change on a false premise. Before executing, the run now asks
  the repository whether the task's branch is contained in the base branch —
  never the plan record, which is the thing that can be stale — and when it is,
  records the task `completed` with `corrected_from`/`corrected_reason` and says
  so. A genuinely unmerged branch still runs as before, a task with no branch is
  unaffected, and nothing is merged on the operator's behalf. (Fixes #325)
### Added

- An orchestrator can now record a task's status outside the loop through the
  planning surface: the `record_task_status` MCP tool (`plan_name`, `task_id`,
  `status`, `who`, optional `notes`) is the machine-side `snodo task complete`.
  Both call one implementation — `PlannerMCP.record_status` — so they share the
  plan's status vocabulary, the provenance (who, when, why) and the audit
  event; a task recorded through either leaves the same `status.json` and the
  same audit entry, and later waves advance from it. The record is an
  operator's account, never a verdict: the audit entry is marked unjudged and
  `outside_loop`, an invalid status is refused exactly as the CLI refuses it,
  and the tool mutates nothing else about the plan. (Fixes #326)

## [0.10.0] — 2026-09-17

### Added

- The coder's own report is now surfaced in the halt payload and in
  `snodo task show`. Disagreements between what the worktree recorded and what
  the coder believed it wrote are plainly named as `claimed-but-missing` (files
  claimed in the report that do not exist on disk) and `unclaimed-but-present`
  (files on disk that the report never claimed). The report is explicitly labeled
  as evidence from the coder's own account rather than an engine verdict. Runs
  without a report remain byte-identical to before. (Fixes #319)

- The litellm coder now fills its own `CoderReport` from what its tool loop
  already holds: the files it staged, the turns it used against
  `max_tool_turns`, the tokens each response reported, its wall time, and why
  it stopped. The stop reason comes from the exits the loop already takes — a
  normal finalize is `completed`, `TurnBudgetExhausted` is `turn_budget`, a
  truncation at `max_tokens` is `context_budget`, and an `LLMCallError` from
  the completion call is `provider_fault` — rather than a second judgement
  about the same event. Nothing changes about what the coder does, submits or
  how it fails, and a report that cannot be built leaves `last_report` absent
  instead of failing the run. Nothing reads it yet (ADR 048). (Fixes #317)
- A subprocess coder is now invited to report its run, and says nothing if it
  will not. The engine cannot count a spawned CLI's turns and will not read its
  prose, so the adapter offers it a path inside the job's own state
  (`.snodo/jobs/<job_id>/coder-report.json`, never the user's project) and asks
  it to write an ADR 048 report there before exiting. After the run the file is
  read, parsed tolerantly by `parse_coder_report`, and deleted whether it was
  written or not. A missing report is the ordinary case and is silent; a partial
  one keeps what it has; a malformed one is discarded with a log line. None of
  these is an error, a halt, or a change to the run's outcome, and the
  invitation is additive — it never changes what the coder is asked to build.
  Reading is confined to the agreed path: stdout and stderr are never parsed,
  and no stop reason is mapped onto a halt (ADR 034/048). (Fixes #318)

- A coder can now say, in structured terms, what a run did and how it ended —
  and the engine has a shape to receive it. `snodo/coders/report.py` (new)
  declares `CoderReport`: the files a coder believes it created/modified/
  deleted, how far it got (turns used against available, tokens against the
  window, wall time — whichever it actually knows), and why it stopped from a
  closed set (`completed`, `turn_budget`, `context_budget`, `provider_fault`,
  `abandoned`). This ticket adds only the shape: no adapter fills it in yet and
  nothing reads it (ADR 048). Every field is optional — a report is best-effort
  evidence from a non-deterministic participant, so a coder that did the work and
  forgot to report is a normal, valid outcome, and a malformed report is discarded
  with a log line by `parse_coder_report`, never a halt. The type carries no
  verdict field and cannot gain one: the worktree stays the only authority on what
  was written and nothing a coder reports may cause a pass — a coder that reports
  nothing is indistinguishable, to any decision, from one that reports.
  (Fixes #315)

- The strict `llm` config is now pinned against the things snodo itself ships
  and writes. ADR 046 made an unknown `llm` key a `ConfigLoadError`, and
  nothing proved that what the tooling produces still loads under that
  strictness — so a newly forbidden key could break a fresh install or every
  `snodo config set` writer with no signal until an operator hit it mid-run.
  Tests now load the default config `ConfigManager` reads and writes, every
  key `snodo config set` accepts, and the full config example the runbook
  prints, against the strict models; keep the deprecated `llm.wave` migration
  a migration rather than an unknown key; and assert the rejection itself under
  every section that forbids extras, naming the key and the section. Every
  shipped template is also compiled, not only checked for well-formedness, so
  a template the graph builder cannot build fails here rather than at a user's
  first `snodo init`. No field, default or template content was changed.
  (Fixes #307)

- `llm.recon.models` is now an ordered failover list in behaviour, not only in
  name. Recon resolves the list into one lane per agent, and a lane tries its
  models in order: a model that fails or returns nothing hands off to the next,
  and the first that answers wins. Previously `resolve_recon_agents` took the
  first `num_agents` entries and called each exactly once, so a second model was
  reachable only by raising `num_agents` — which fanned out to both models in
  parallel instead of falling back to the second. `snodo survey` was stricter
  still, reading only `agents[0]`, so every entry past the first was dead on
  that path. A model that answered is never retried: a poor answer is an answer,
  and failover is for silence and faults. The deliberate fan-out is preserved —
  `num_agents > 1` still runs several models in parallel to compare answers, and
  an explicit `agents=[...]` list still means exactly those agents. Each result
  records the models that were tried and why each was passed over, and the
  resolution warnings now name the unused tail when more models are configured
  than `num_agents`, instead of warning per empty slot when fewer are. No new
  state, severity, halt type or task status was added. (Fixes #302)

- `snodo models --benchmark` times one fixed prompt against one model and
  reports output tokens per second, time to first token and total wall time, so
  two providers produce two numbers produced by the same work. Until now the
  only throughput figure came from `--stats`, which aggregates job telemetry
  across whatever prompts happened to run — a number that moves with prompt
  size, tool-call count and how much the judge read, and that was used on a real
  project to switch the coder between providers twice without a like-for-like
  comparison. The prompt is part of the measurement, not an argument to it: it
  lives in `snodo/cli/commands/model_benchmark_prompt.txt`, is read from the
  repository rather than assembled at runtime, and is the same every run
  (changing it makes past numbers incomparable, which the file states). The
  command is scoped by the flags that already select a model (`--provider`,
  `--id`, `--id-contains`, the cost/context bounds), requires exactly one model, and
  reports both decode throughput (after the first token) and overall throughput
  (including the first-token wait) because a model that streams fast after a
  slow start is a different proposition for a coder than for a validator. It
  makes one real, billed API call, reachable only through `--benchmark`, and
  prints the model, the prompt identity and a spending notice before the call.
  The output names the token-count basis — provider-reported usage when
  available, otherwise a local tokenizer — so a comparison is never between two
  different accounting systems. No new state, halt type or status value. (Fixes #284)

- A closed-vocabulary check: a new severity, halt type or task status fails the
  gate until a decision record is written for it. ADR 045 records that the
  engine's vocabulary is closed — a validator returns one of three severities, a
  halt resolves to a fixed set of outcomes, a task carries a fixed set of
  statuses — but nothing enforced it. The lesson the record preserves is the
  abstention `severity=None`: a value added once that spread to twenty-one call
  sites across six packages before anyone questioned it, and cost sixteen
  hundred lines to remove. A document prevents the next one only if someone
  reads it at the moment they are adding a value, which is exactly when nobody
  does. `scripts/enforce_vocabularies.py` derives the three vocabularies from the
  code — the `ValidatorResult.severity` annotation, the `_CANONICAL_HALT` map,
  the task-status anchors — and fails with a message naming the value and the
  vocabulary it joined, saying that widening it is a decision, not an edit.
  Today's values are recorded in `scripts/vocabularies_baseline.txt`; removing a
  value passes, and `--update-baseline` refuses while the check is red so a new
  value cannot be laundered in. It runs in the local gate, the CI gate and the
  release. (Fixes #280)

- `snodo intake` proposes validator criteria from a repository's own decision
  records, one at a time, each naming the record it came from, and writes the
  protocol only after the operator accepts. Survey derives the extension of
  governance from code — which modules exist, how each is verified, where
  decisions live — and deliberately does not derive the protocol's normative
  content, because that content is prose a human wrote. But the prose is often
  in the repository: a real project's decision records stated the very rules
  its protocol encoded by hand. The boundary is not "a human must author the
  normative part" — it is that intake never opened the records. Intake does,
  and offers what they state. A sentence is proposable only from a record's
  **Decision** section: Context is background, Consequences are effects rather
  than rules, Alternatives considered is the road not taken, and Status is
  metadata, so none of them is offered and a record with no Decision section
  proposes nothing. The citation discipline is the one a boundary judge
  already uses on source files — every proposal cites its record, resolved
  against the repository before the proposal is built, so a criterion without
  a record is never proposed. `snodo intake --json` reports the proposals and
  writes nothing; `--reject-all` writes nothing; `--validator <id>` chooses the
  target (default: the protocol's architecture validator, else its first).
  This is a sibling of survey rather than part of it: survey's contract is that
  it writes nothing, and making that conditional would weaken it for every
  caller (Fixes #259).

### Changed

- The run stream now distinguishes the kinds of line the engine already emits —
  phase boundaries, validator and coder tool turns, coder activity, per-validator
  gate verdicts and terminal recovery halts — and compacts repeated tool-turn
  lines in place on an interactive terminal. A run that produced a screenful of
  identical `[0:14] Turn 9: list_files(...)` lines, then a phase change, then a
  halt, no longer makes the operator read every line to find the two that matter:
  the halt and the phase carry their own styling, and the turns overwrite one
  another as a moving latest turn. This is a presentation pass over information
  the sink already carries, not new telemetry: classification reads only the
  shape of a line, and no state, severity, halt type or task status is added.
  Colour and compaction are decoration for a tty only — a piped or redirected
  stream, a non-tty stdout, and `NO_COLOR` all produce byte-identical plain lines
  to before, and the same lines reach the log and the audit trail in the same
  order. The `snodo logs --watch` path renders the same way. (Fixes #294)

- An MCP tool is no longer gated on a token the caller must hold. Every tool
  schema carried a `requires_token` flag and `_enforce_wf1` refused the call
  when the server held no validation token — but which tools a caller may use
  is the protocol's and the mode's to decide, and the validator quorum is
  enforced inside the engine loop, per task. The flags and the surface gate
  are removed (`requires_token`, `_enforce_wf1`, the `WF1 violation` refusal
  and the `wf1_violation` audit event); `validate_task` still runs the real
  pre-execute quorum and a `pass` still records the single-use token the next
  `dispatch_task` consumes as the audit link. The engine's token issuance,
  verification and the human `authorize` path are untouched, and so are the
  four validation outcomes. The server instructions now say what is true:
  access is the mode grant, the guarantee is the loop (ADR 047, superseding
  ADR 017 as the record of the tool surface). Notably, `propose_plan`,
  `decompose` and `generate_spec` — authoring calls that execute nothing —
  are no longer refused for want of a verdict about a task that does not
  exist yet. (Fixes #312)

- The rule that binds a model name for litellm has one home. Every completion
  this system issues must bind litellm's routing name together with its
  `api_base` and `api_key`; a provider block named for itself is not a provider
  litellm knows. That rule was written three times, and the third copy —
  `protocol_adherence` — passed the configured name straight through, so a
  validator failed against a gateway-style provider while the engine path, which
  had it right, worked. Two implementations that disagree, one of them wrong, is
  the worst arrangement to debug. `build_completion_fn` in the validator runner
  is now the single implementation; `_build_completion_fn` in the engine loop is
  gone and its four call sites import the shared factory, and
  `protocol_adherence` no longer overrides the bound name. A guard test walks
  both package trees and fails if a second implementation reappears under any
  name. (Fixes #255, Fixes #256)

### Fixed

- A spec redefinition now keeps the evidence the original carried. When a
  `judges_spec` validator sends a spec back to be reauthored, the rewrite is
  what every later validator judges and what the coder is given, so anything
  it dropped was gone from the run. The rewriter named the file and line where
  a symptom was seen one attempt and not the next: the citation was removed,
  the task was broadened to the ground the citation had bounded, and the next
  validator escalated on a task vaguer than the one it was rewritten from. The
  reauthored spec now carries its concrete anchors — file paths, `path:line`
  citations, line references, named tests and code literals — forward
  verbatim: they are named to the author to reword the ask around, and any the
  author drops are put back before anyone downstream sees the spec, with what
  was restored recorded in `spec_authoring`. A spec that carries no evidence is
  reauthored exactly as before, and the attempts bound, the `judges_spec`
  filter and the rewrite path are unchanged. (Fixes #320)
- `intent.yml`'s `spec-manners` validator warned specs inconsistently on its
  "code-prescriptive" criterion: the wording asked a judge to separate
  evidence (a file and line naming where a symptom was OBSERVED) from
  prescription (a transcribed implementation) without ever stating the
  distinction, so a judge scanning for "mentions code" flagged both alike. On
  a real project this produced opposite verdicts for two tasks of the same
  shape. The criterion now says the distinction itself: evidence grounds a
  claim about the present state and is allowed; prescription dictates the
  shape of the change (a described sequence of edits, or code the coder is
  meant to reproduce) and is what gets warned, with the judge asked to cite
  the specific prescriptive text. (Fixes #322)

- A gate's output now lands in rows on the operator's terminal instead of
  marching off as a staircase. #304 ran the gate over a terminal (`ssh -tt`) so
  a hangup reaches the remote process group, but `ssh` also copies the
  operator's raw termios onto the remote pty, which leaves `opost` off — and
  with `opost` off `onlcr` is inert, so the `stty onlcr` the wrapper had been
  making was a no-op and every bare LF moved down a row without returning to
  column zero. A full pytest run printed its progress as a diagonal that
  wrapped and overwrote until it was unreadable. `gate_supervise` now sets
  `stty opost onlcr` on the connection's tty (only where one exists) and no
  longer discards its error, because the output's readability depends on it.
  The checks, their order, the exit status and the #304 lifetime behaviour —
  an interrupted gate still leaves nothing running on the host — are unchanged,
  and a clean gate prints exactly what it always did. (Fixes #310)

- A gate run now ends on the gate host when it ends on the operator's. `make
  gate` and `make gate-ci` ran `ssh` with no terminal and nothing tying the
  remote process tree to the connection, so an interrupted gate, a dropped
  link or a killed `make` left the remote pytest and its workers running with
  no parent watching them. A few abandoned runs plus a few live ones
  oversubscribed the host until forking a login shell took tens of seconds —
  sshd still accepted connections, so the box answered ping but looked
  unreachable over SSH. The gate now runs over a terminal (`ssh -tt`) so a
  hangup reaches the remote process group, and `scripts/gate_remote.sh` (new)
  traps that hangup and reaps the whole tree, including children that
  daemonise (`snodo.jobs.runner.spawn_background` starts jobs in a new
  session). The checks themselves, their order, the per-worktree gate
  directory and the exit status are unchanged, and a clean gate prints exactly
  what it always did. Concurrent gates are bounded rather than forbidden: two
  `flock` slots per host, so several worktrees can still gate at once without
  saturating it. (Fixes #304)


- An `llm.*` config key that is not a setting of its section now fails loudly
  instead of being silently discarded. The `llm` section is owned end to end by
  the engine, so `load_llm_config` rejects any key that is not a field of the
  owning model (`CoderConfig`, `ValidatorConfig`, `ClassifierConfig`,
  `ReconConfig`, `WaveConfig` or `LlmConfig` itself) with a `ConfigLoadError`
  naming the key and the section and listing the section's valid keys. The
  reported failure: a validator section carried `single_max_tokens` and
  `temperature`, neither a field of `ValidatorConfig` and both explicitly out
  of scope in `docs/specs/llm-config-section.md`, and both parsed, did nothing
  and said nothing — the operator believed a budget and a temperature were
  configured for weeks. A typo and a knob that was never implemented were
  indistinguishable. The deprecated `llm.wave.max_tokens` / `temperature`
  migration still runs before validation, so a supported migration keeps its
  values and its `DeprecationWarning` and is never reported as an unknown key.
  No default, field, state, severity, halt type or task status was added.
  (Fixes #298)

- `snodo survey` now says what it is waiting on while the boundary-judgement
  agent call is in flight, instead of leaving a dead terminal. The call goes
  out through the recon machinery and can take tens of seconds on a slow or
  unreachable provider, during which the command printed nothing — so
  thinking, a stalled request and a hung process looked identical, and the
  reasonable response was ctrl-c, discarding work that was about to succeed.
  A one-line indicator on stderr names the agent being waited on and erases
  itself when the call returns. It is decoration, not output: it is written
  only when stdout is a terminal and never under `--json`, so the report, the
  machine interface and a piped run are byte-for-byte what they were. It is
  funnelled through the engine's `ProgressSink`, so a broken indicator is
  reported once and cannot take the run down. A failed or empty agent result
  still prints exactly the diagnostic it printed before. No state, severity,
  halt type or task status was added. (Fixes #292)

- A coder run now records which binary produced it, a missing coder says which
  PATH it searched, and a stopped server no longer leaves a child holding its
  port. An operator spent an hour discovering that their coder was running a
  version of `opencode` they had already uninstalled: two installs existed, the
  older one earlier on the PATH a long-running `snodo serve` had captured hours
  before, so every dispatched job used the old binary — and nothing in the halt
  payload said which binary had run. The subprocess adapter now resolves the
  binary in the process that will invoke it, at the moment it runs, and records
  its absolute path and self-reported version (`coder_binary`/`coder_version`)
  on the artifact and in the halt payload, so two installs of the same tool are
  distinguishable after the fact. `CoderUnavailableError` names the PATH that
  was searched, which is what makes "not on PATH" actionable when the operator
  can see the binary on theirs. `snodo ready` reports the resolved install and
  version (a new unscored INFO finding) rather than only that some `opencode`
  exists. On the server side, the tunnel's MCP and cloudflared children are
  spawned in their own process groups and the whole group is torn down on stop,
  so nothing either started outlives it; and `snodo serve` names the process
  holding a port and how long it has held it instead of surfacing a raw
  `[Errno 48] address already in use`. PATH resolution is unchanged — being
  silent about its result was the defect, not resolving it at invocation. No
  halt type, severity or status value was added. (Fixes #290)

- A recovery attempt whose spec asserts evidence the tree no longer holds is
  reported instead of dispatched. A spec that opens "the booking field is
  `<input type="url">`" is making a checkable claim, and an earlier attempt can
  have fixed exactly that while a later attempt is still sent to find the
  defect — observed on a real project, where attempt 4 spent an hour proving the
  premise false before running out of time. Before spawning a recovery subtask
  the engine now reads the spec's presence claims (a code construct introduced
  by a copula or stative verb, with any preceding modal disqualifying it, and
  bare identifiers left alone) and checks whether the construct still appears
  anywhere in the worktree. When it is gone the recovery halts with the
  existing `escalated` outcome and the citation in the record; the engine never
  rewrites the spec, because deciding what the task now means is not its call
  (#35). Absence is only reported on a complete scan, so a false stale verdict
  is never preferred to a missed one. No halt type, severity or status value was
  added. (Fixes #286)

- A coder run that exhausts its **turn** budget has its produced work judged
  instead of assumed absent, exactly as #281 made a timed-out run's work judged.
  The two bounded outcomes — the clock and the turn budget — sat side by side in
  the executor's `try` block and said different things about the same kind of
  event: `CoderTimeoutError` consulted the task-branch work-recovery probe and
  carried any work it found into post-execute validation, while
  `TurnBudgetExhausted` propagated unchanged with `artifacts_count 0`. Both now
  route through one shared probe, so the two paths cannot drift apart again. A
  turn-budget exhaustion also no longer canonicalises to `blocker`, which read
  as a verdict about code no judge saw; it resolves to the operational
  `environment_error` (ADR 015), and its hint names the run's turn bound rather
  than the spec, the code or an install. The run fact is still recorded when the
  recovered work passes, so a bounded run never proceeds silently. No halt type,
  severity or status value was added. (Fixes #282)

- A judge told to decide is no longer handed the read tools while it does so.
  #278 withdrew the read tools on the final turn, but the nudge after a prose
  answer still offered the full read-only set and only narrowed at the last
  turn. Observed on a real project: an acceptance judge on a 44-turn budget
  answered in prose at turn 32, was asked for its verdict, and read for twelve
  more turns — the tools it kept choosing from were withdrawn only at turn 44,
  by which point it submitted nothing and the run failed closed. The nudge and
  the final turn are the same moment for this purpose: the loop has decided the
  reading is over, so the turn after the nudge now offers `submit_verdict`
  alone, states that the read tools are gone, and says that a verdict on a
  partial view is real (`warn` exists for it). A judge that decides at the
  nudge returns a real verdict; one that still returns nothing fails closed,
  unchanged. No halt type, severity or status value was added. (Fixes #285)

- A halt names `snodo authorize <task_id>` only when there is a decision to
  sign. The escalate hint and the CLI follow-up were derived from the halt's
  shape, so a halted task printed `Follow-up: snodo authorize <task_id>` even
  when no decision existed; running it answered "No pending decision for task
  ..." and the task never appeared in `snodo authorize`'s list. Observed on a
  real project: a halt printed the command, the audit log carried 3,792 events
  and not one decision record naming the task, and the operator spent the time
  that the halt itself needed looking for a hatch that was not there. The
  accept-rule now lives beside INV3 in `snodo.infrastructure.decisions`
  (`is_adjudicable_proposal`: a `set_model` proposal, or an `adjudicate`
  whose severity is not `blocker`, since a blocker is non-overridable) and the
  engine records an `adjudicable` flag on the payload from the session that
  actually holds the proposal. The hint and the follow-up name authorize only
  when that flag is set, and otherwise say what the operator can do instead. No
  state, status or halt type was added, and nothing mints a decision to make the
  suggestion true. (Fixes #288)

- A judge returns a verdict, and a non-verdict is an error. The abstention
  state — `severity=None` plus `abstention_reason`, `unexamined_tools`,
  `last_words`, `abstention_policy`, two halt types, an in-place re-judge loop
  and two decision records — began as #252's correct worry that a judge which
  did not decide must not be reported as a pass. The remedy was wrong: `error`
  already existed and already failed closed, and the invented state spread to
  twenty-one call sites across six packages. Observed on a real project: five
  consecutive runs of one task where the architecture judge read fourteen files,
  answered in prose, was asked for a verdict, and went back to reading — because
  the loop offered every read tool on every turn, including the turn after it
  asked the judge to stop and decide. The judge is now made to decide: the final
  turn offers `submit_verdict` alone, the read tools are withdrawn, and the judge
  is told that a verdict on partial reading is a real verdict (`warn` is what it
  is for). One that still returns nothing is an error and fails closed. Removing
  the state took 1,606 lines with it. `ValidatorResult.severity` is now required.
  ADR 045 supersedes ADR 042 and ADR 043 and records why the vocabulary is
  closed. (Fixes #278, and with it the non-blocking denominator arithmetic of
  #277, which described a policy that no longer exists.)

- A timed-out coder run has its work judged rather than discarded. A run that
  finishes and then hits the clock is an operational outcome, not a verdict about
  the code. Observed on a real project: the coder fixed the field, wrote the
  inventory document, added the regression test, ran the suite green and reported
  "Done" — then the 3600s timeout fired. The engine raised a generic
  `LLMCallError`, recorded `artifacts_count: 0`, skipped post-validation and
  halted the task as a blocker with a hint to fix the coder's install. The
  finished deliverables sat on the task branch while the plan reported the task
  blocked, and a day was spent on it. A timeout now raises `CoderTimeoutError`
  and the executor consults the existing task-branch work-recovery probe before
  declaring a fault; work found there is carried into post-execute validation
  exactly as freshly produced work would be, so the judges decide instead of the
  engine assuming. A timeout belongs with the coder that could not be invoked —
  it reports as the operational halt it is, and no halt type was added for it.
  (Fixes #281)

- A completed job's worktree and branch are torn down. #275 moved worktree
  creation to the task identity; the teardown was not moved with it, so the
  wrapper asked git to remove a worktree that was never created under that name,
  found nothing, and left the real one registered — and a branch checked out in a
  worktree cannot be deleted, so both survived a clean merge. A real project
  accumulated around a hundred worktrees and as many `task/*` branches this way,
  which is also what made `snodo task list` unreadable. The identity has one home
  now, `resolve_task_identity`, used by both creation and teardown, and teardown
  is one shared sequence: worktree first, then only branches whose work is
  already in the base. A failed or blocked run still preserves both for
  inspection. (Fixes #276)

- Task status is settled by git containment rather than by an audit event alone.
  A task branch still on disk was read as work in flight however long ago it
  landed, and `merged` was reachable only through a `task_merged` event, so a
  task whose event was absent or keyed differently stayed `in_progress` forever.
  Observed on a real project: 161 task branches, 148 of them fully merged into
  main, every one of them reported in progress — and 113 of those were named for
  a job rather than a task, duplicating work that was already listed correctly
  under its own id. Containment in the base is now the authority, and a
  job-executed task appears once, under the task identity. (Fixes #275)

- A blocked plan task names the job that reaches its logs. `snodo plan status`
  reported a blocked row and `snodo logs <plan_job>` said to inspect
  `<child_job_id>` without naming one, so the only way from a blocked task to its
  output was to grep the jobs directory by hand. Rows that need a look now carry
  the command to look; healthy and never-run rows stay clean. Following a plan
  remains one command — status points at `snodo logs <plan_job> --watch` rather
  than shipping a second way to watch. (Fixes #279)

- Opening a git repository no longer leaves a persistent `git cat-file` child
  behind to be terminated later. GitPython's default object DB starts a
  long-lived `git cat-file --batch-check` subprocess on the first object read
  and ends it only from `Repo.__del__`; because `Repo`/`Git` form reference
  cycles, that finalizer runs on a later cyclic-GC pass — an unbounded time
  after the work that needed it, and possibly inside an unrelated task or test.
  The child dies by `Popen.terminate()`, i.e. `os.kill(pid, SIGTERM)`, which is
  how a test suite came to signal a process it never created: under `-n 24`,
  `test_cancel_running_job` patched `os.kill` process-wide and intermittently
  recorded extra SIGTERMs from a `git cat-file` child that another test had
  orphaned earlier. Every repository open now goes through
  `snodo.tools.git.open_repo`, which uses GitPython's in-process object DB and
  is used as a context manager where the work is function-scoped, so no such
  child exists to outlive its work. A new autouse guard (`Fixes #258`) fails any
  test that leaves a live child process or non-daemon thread running into the
  next one, so a future leak is caught where it is introduced. The
  `test_cancel_running_job` assertion stays `assert_any_call` — that is the
  correct statement of its intent, independent of this leak.

- The reduced-gate banner states its count or says nothing, and appears only
  when a reduction happened. It exists to say what did not run, and was wrong in
  both directions: under `pytest -n` the deselected count interpolated as empty,
  leaving "the end-to-end (e2e) suite were deselected" — ungrammatical, and
  missing the only quantitative thing it said — because the deselection happens
  in xdist workers and the controller had no tally to report. And it printed on
  any run whose marker filter excluded e2e, including scoped runs like
  `pytest tests/jobs` that contain no e2e test at all: it announced a reduction
  that never happened and listed three CI-only gates as the reason. A warning
  that is wrong on the runs where it is least needed teaches a reader to skip
  it, and then it fails on the run where it was right. (Fixes #257)

- The change section handed to a judge is budgeted from what changes here
  actually weigh. `CHANGE_SECTION_CHAR_LIMIT` was set at 24,000 characters
  without measurement; across the last forty non-merge commits on main (p50
  21,444, p75 34,004, p90 53,487, max 103,626) that truncated 45% of them, so
  the "PART OF the change" path introduced for lockfile-and-bundle outliers had
  become the norm — and a judge routinely told its view is partial is a judge
  pushed back toward reading the tree, which is what handing it the change was
  meant to stop. The limit is now 64,000 characters, leaving 95% of this
  repository's commits untruncated. The per-file cap moved with it, 8,000 to
  16,000: measured against 241 non-bulk source chunks the old cap was cutting
  14.1% of hand-edited files — including a 31,030-character one — which is the
  content a judge most needs. Both distributions are recorded beside the
  constants. (Fixes #274)

## [0.9.0] — 2026-09-14

### Added

- A docs-coverage ratchet: every invokable surface must be mentioned in the
  docs, and every unambiguous reference in the docs must name something that
  exists. Documentation goes stale silently and silence reads as authority — in
  one release the halt contract gained a fifth outcome, `run_plan` changed from
  a blocking call to returning a job id, and a validator tool plus two commands
  shipped with no page mentioning them, caught by nobody but a person
  remembering. `scripts/enforce_docs_coverage.py` walks the live Typer tree, the
  MCP tool registry and the settable config surface, and checks both directions
  against `docs/`; which page covers an item stays an editorial choice.
  `docs/decisions/` is exempt as a record of a moment, and `docs/specs/` is
  excluded from the published site. Existing gaps are baselined in
  `scripts/docs_coverage_baseline.txt` and may only shrink; a dangling
  reference is never baselined. It runs in the local gate, the CI gate and the
  release. (Fixes #266)

- `snodo logs <job_id> --watch` follows a plan run. A plan job is a pure
  orchestrator — it spawns a child task job per task and writes nothing to its
  own stdout — so following one tailed an empty file indefinitely with no
  indication that the output would never come. Following a plan job now shows
  the plan's wave structure and streams per-task lifecycle updates (task, status,
  child job id, duration), ending when the plan does; per-task detail stays one
  `snodo logs <child_job>` away, because three concurrent tasks interleaved on
  one screen is unreadable. This keeps the contract that any job id is followed
  the same way, rather than requiring the reader to discover the plan name and
  switch commands. Separately, following any job that produces no output of its
  own now says so immediately instead of blocking or exiting silently.
  (Fixes #272)

- Halt payloads now record the attempts a task took, not only its final
  verdicts. A task that passed first time and one that passed on the fourth
  attempt after three silent judges previously produced payloads a reader could
  not tell apart — observed on a real project whose completed payload reported
  a unanimous pass and `abstain_count: 0` despite four attempts over
  forty-five minutes. The payload gains an `attempts` summary: `total`,
  `non_verdicts`, `coder_dispatches`, and a bounded `history` of
  `{attempt, outcome}` entries using canonical outcomes (`passed`, `warned`,
  `blocked`, `abstained`, `error`). It recovers the two categories the final
  results hide — prior recovery attempts (from `prior_failures`) and the
  in-place abstention re-judges that dispatch no coder and create no subtask.
  Halt types and the final `validator_results` are unchanged; the summary is
  the history that precedes them.



- `make wt-check`, `make wt-rebase` and `make wt-merge` for the agent
  worktrees. `wt-check` reports what is unmerged, stale or uncommitted across
  every worktree; `wt-rebase` rebases onto `origin/main`, refusing rather than
  rebasing over uncommitted work; `wt-merge` rebases, gates and merges each
  selected worktree in turn and stops at the first failure, so a worktree whose
  gate fails never has another merge stacked on top of it. Selection travels in
  `W` (`make wt-merge W=a,c`).

### Changed

- The README is a two-minute introduction rather than a reference manual. It
  carried the protocol language, the coder catalogue, the full command
  reference, the architecture and the configuration surface — 289 lines a
  developer had to read before knowing whether the project was for them. It is
  now 158: the problem, what snodo is, the shortest install-to-run path, and one
  real reproducible transcript whose gates are not stubbed. Reference content
  moved to the pages that own it, each leaving a sentence and a link —
  configuration, which had no home, into the runbook alongside readiness, retry
  semantics and the command map; the coder table into the runbook and
  `docs/index.md`, which became the authoritative hub carrying measured status
  rather than an overflow bin. The honest boundaries stay up front: the
  container opencode path is experimental, the repository is trusted rather than
  sandboxed, and this is a preprint-stage research artifact. (Fixes #265)

- Every post-execute validator is handed the produced change. It previously had
  no way to learn what the coder had done: the diff preload existed only when a
  project's protocol granted `read_diff_between_refs`, and never on the
  single-completion path, so a judge asked to assess the work first had to find
  it by reading the tree. Observed on a real project: an acceptance judge ran
  41, then 49, then 44 turns reconstructing the change file by file and reached
  no verdict in any of them, then passed at turn 26 on a fourth attempt where it
  happened to open the changed files early — the same work, different luck. The
  distinction is now the evaluation phase, not the tool grant: `run_validators`
  reads `base_ref..HEAD` once per pass and every post-execute judge, tool-loop or
  single-completion, granted or not, begins with a `## Code Change` section
  already present, falling back to the engine-recorded file list when no git
  range exists. Pre-execute judges review a proposal and never receive one. Tool
  grants continue to govern capabilities unchanged — an ungranted
  `read_diff_between_refs` is still neither offered nor callable. (Fixes #267)

- An abstention keeps the judge's last words. A judge that answered in prose
  instead of calling `submit_verdict` had that prose dropped: the record showed
  that it declined to answer but not what it said, which is the single most
  useful artefact of a failed judgement. The closing account is now kept,
  bounded, alongside `severity=None`. It is never mined for a finding or a
  severity — a judge that did not submit a verdict did not reach one.
  (Fixes #270)
### Fixed

- Read tools can no longer reach above the workspace root, and no longer offer
  version-control internals or derived build output as though they were the
  work. Two problems wore the same clothes. Containment: direct `../` escapes
  were refused, but a symlink the tree-walking tools met was not — a workspace
  containing `src/escape -> /etc/hosts` let `search_string` and
  `summarize_directory` read `/etc/hosts` — and `validate_path` compared path
  parts against `".git"` case-sensitively, so on a case-insensitive filesystem
  `.GIT/logs/HEAD` opened the real reflog. Judgement: `.git` internals and
  derived output are inside the workspace and readable by design, but they are
  not the work; a judge that read them spent budget to learn nothing, and in
  the `.git` case reasoned about history it was never meant to see. Observed on
  a real project: a judge called `list_files("..")`, read `.git/logs/HEAD` to
  reconstruct the branch history, then read `dist/styles/card.css` and
  `dist/scripts/render.js` at length — compiled copies of source it had already
  read. Every read tool now funnels through the one boundary; a refused path
  raises `PathValidationError` naming the reason (version-control bookkeeping /
  derived build output) rather than returning content or silence, and a listing
  simply does not offer what is not the work. Derived output is distinguished by
  what the project already declares about itself — its git ignore rules: a path
  git ignores and does not track is derived output, read once per tool call
  (`git ls-files --others --ignored`). The cost is recorded where the choice is
  made: a project that does not use git declares nothing and gets no
  derived-output filtering; a project that gitignores source it wants read must
  track it; a gitignored-but-force-added file stays readable because git tracks
  it. `.snodo/` remains readable (ADR 026). An in-place source tree that keeps
  tracked files under an ignored-looking directory name — the case a fixed
  directory-name list would have hidden — is unaffected. (Fixes #273).

- A post-execute abstention no longer spawns a coder recovery. An abstention is
  a judge reporting that it did not reach a verdict, not a finding about the
  code, so routing it to the recovery machinery re-ran the coder at work no
  judge faulted and handed it a recovery spec describing a failure that was
  never diagnosed. Observed on a real project: a task completed, its acceptance
  judge abstained, and the engine spawned three recovery attempts over
  thirty-five minutes; each dispatched the coder, found nothing to change
  ("git add staged nothing"), and on the fourth acceptance run the judge passed
  on that same untouched code. Four coder dispatches, one of which mattered. An
  abstention now re-runs the abstaining judges in place on the unchanged work —
  the coder is not dispatched and no recovery subtask is created. The retry is
  bounded by the mode's `max_recovery_depth` (a protocol that permits no
  recovery permits no re-judging), and a repeated abstention is treated as a
  stall whatever prose accompanies it: the verdict signature canonicalises an
  abstention to a single marker, so a judge that abstains twice with different
  justifications no longer looks like progress. When the bound is spent the
  task halts as `abstention_exhausted` / `abstention_stalled` and the CLI
  follow-up offers `snodo authorize` rather than a coder retry. `abstention_policy`
  is unchanged, and an abstention is never converted into a pass.

- The change section handed to a judge is bounded. Since #267 every post-execute
  judge receives the produced change as a prompt section, and it was handed at
  whatever size the diff happened to be: git returned it whole, it was stored
  whole, and the prompt inlined it whole. A wave that regenerates a lockfile or
  a bundle beside three source files therefore pushed tens of thousands of lines
  into every judge, including the single-completion ones with no turn budget to
  recover with — failing as a provider context-length error or a silent
  mid-hunk cut that left the judge reading half a change it was told was whole,
  both harder to recognise from a log than the wandering #267 removed. The
  section is now at most `CHANGE_SECTION_CHAR_LIMIT` (24,000 characters) for a
  change of any size. A diff that fits is shown verbatim. One that does not says
  it is showing part of the change, names every changed file first — the names
  are small and are what a judge most needs — and spends the remaining budget on
  content, each file capped at `CHANGE_FILE_CHAR_CAP`, likely-regenerated bulk
  giving way to hand-edited source, with explicit markers wherever content was
  cut or dropped. The bound is on the section the engine injects unbidden;
  `read_diff_between_refs`, which a judge calls deliberately, is untouched.
  (Fixes #269)

## [0.8.4] — 2026-09-13

### Added

- The README is now a two-minute introduction instead of a 289-line manual. A
  reader arriving at the repository met the protocol language, the coder
  catalogue, the full command reference, the architecture and the configuration
  surface before learning what the project was for. The introduction states the
  problem, shows the shortest install-to-run path, and gives one real
  reproducible transcript: four sentences of what snodo is, a mock run whose
  gates are not mocked, and honest status and threat-model boundaries up front,
  including that the container `opencode` path is experimental. The reference
  material did not disappear — it moved to the pages that own it, each leaving a
  sentence and a link where it was. The configuration surface had no home and
  now lives in the runbook; `snodo ready`, the retry/`--append-spec` semantics
  and the command map moved there too. `docs/index.md` became the authoritative
  hub rather than an overflow bin, carrying the measured project status
  (code size, `radon` complexity, the CI gates) and the coder-backend summary.

- `snodo survey` now reads a governed repository instead of refusing to look at
  one. Survey printed "This repository already has a protocol" and exited with
  the code that means something broke inside the tool, so a caller could not
  tell "this repository is governed" from "the survey crashed", and the more
  valuable question went unasked: a repository without a protocol asks what
  governing it would mean, and one with a protocol asks whether that protocol
  still describes the code. The analysis is the same analysis; only the closing
  question differs. `snodo.survey.drift` compares what the protocol declares
  with what the survey observed — a module path the protocol governs that no
  longer exists, a boundary the code shows that no declared module reaches, a
  `decisions_path` that has moved or emptied, a `tooling.test_command` whose
  runner the repository no longer declares — and reports each subject once
  however many modules name it. Agreement is reported as plainly as divergence:
  `agreements` sits beside `divergences` in `--json`, and a healthy governed
  repository's summary says so rather than listing only problems. Every
  comparison the protocol's shape does not support is listed under
  `not_compared` with its reason. Two shapes of protocol are tested because one
  of them is the common case: modules are optional (ADR 041) and most real
  protocols declare none, so for a module-less protocol the three module
  comparisons are reported as not made rather than as a page of findings that
  mean nothing, and no discovered boundary is called ungoverned. A validator's
  `tooling` map is not a set of paths: only the `test_command` key is read, its
  tokens are matched against a closed runner vocabulary rather than resolved as
  filenames, and the report names the keys it left alone —
  `openai/gpt-4o-mini` contains a slash and denotes a model. A drift report is
  a successful run: survey exits 0 whether or not it found drift, and a caller
  reads `analysis.drift.has_divergences` rather than the exit code; 4 is now
  reserved for the governed case that really is a failure, a protocol that
  exists but will not load. Survey still writes nothing — not the protocol, not
  a proposal file, not a suggested diff — and offers no reconciliation: drift is
  reported to a person who decides. Observed on a real project: a governed
  repository of six modules, seven validators and no `modules:` section could
  not be surveyed at all, and holding the protocol file aside was the only way
  to see what survey would have said. An ungoverned repository's report is
  unchanged, byte for byte, and is pinned that way by a captured baseline in
  `tests/survey/baseline/`.
- The survey's accuracy claim is reproducible. Precision and recall were
  measured once, by hand, against eight private repositories, and nothing in
  the repository could re-run that measurement or notice if a refactor moved
  it. There are now two artefacts instead of a memory. `snodo.survey.measure`
  turns a survey and a declared ground truth into true/false counts and the
  precision and recall derived from them, for module boundaries, languages and
  confirmed test commands — one arithmetic shared by every caller, calling no
  model and reaching no network. `scripts/measure_survey.py` runs that
  arithmetic over real repositories named in a ground-truth file that stays
  outside this repository, so re-checking the published figures is one command
  rather than an evening. And a fixture corpus in `tests/survey` holds the nine
  synthetic trees that actually discriminated between right and wrong answers
  during the original evaluation: a documentation directory with its own static
  site manifest, a test directory with a playwright config, a nested example
  package, a vendored `Pods` tree, a `pubspec.yaml` project, sibling manifests
  with no root workspace declaration, npm's no-test scaffold, source trees in
  `.svelte`, `.astro`, `.swift` and `.dart`, and a manifest that does not parse.
  The expected answer lives in one declared ground truth rather than in
  case-by-case assertions, so the test reports figures — judged boundaries at
  100% precision and recall, the deterministic pass at 8/11 precision with full
  recall, language recall at 100% — and a regression appears as a figure moving
  rather than as an example that happens to still pass. A deliberately wrong
  entry in the ground truth fails, naming the repository and the boundary.
  Judgement behaviour is exercised offline through the judge callable the
  analyzer already accepts: a verdict citing a file that does not exist is
  refused, an abstention is reported and leaves the deterministic answer
  standing, and a reply cut off mid-object neither promotes nor drops a
  subject. One honest gap is recorded rather than papered over: a `pubspec.yaml`
  manifest is still counted as a `yaml` source file, so language precision in
  the corpus is 13/14, and the expectation says so instead of being adjusted to
  match.

- A remote verification gate. The suite is IO-bound and parallelises well, but
  a dev Mac has four cores, and the reduced local run had grown to well over
  two minutes — long enough that it stopped being run before every merge, which
  is the only way a gate protects anything. `make gate` pushes the current HEAD
  to a large Linux host and runs the same checks there in about half a minute;
  `make gate-ci` runs what CI actually decides on, the full suite with coverage.
  Each working copy gets its own directory on that host, named after the
  directory it runs from, so several worktrees can gate at the same time without
  sharing a checkout or a virtual environment. The gate refuses a dirty tree
  rather than testing HEAD while the operator believes it is testing their
  changes. Running on Linux also makes the local result more predictive than the
  same suite on macOS, since that is the platform CI uses. `GATE_HOST` and
  `GATE_HOME` point it elsewhere.

- A file that grows past a thousand lines of code now fails the gate. Nothing
  in this repository watched how large a file was getting: ruff implements no
  module-length rule at all, the selected rule set contains nothing structural,
  and import-linter watches direction rather than size. A limit believed to be
  in force had not been, and one command module went from 569 lines in June to
  1,759 in September with no signal at any point. `scripts/enforce_file_length.py`
  counts lines of code through the tokenizer, excluding blank lines, comments
  and docstrings — a file that is mostly explanation is in good health, and a
  check that punished prose would apply steady pressure to delete the best part
  of this codebase. Two files exceed the limit today and are recorded in
  `scripts/file_length_baseline.txt` with the counts they have; those numbers may
  fall and never rise, a file that drops under the limit leaves the list and is
  then held to the limit itself, and there is deliberately no suppression
  mechanism. The check runs in the local gate, the CI gate and the release, and
  a failure names the file, its overage and its baseline.

- Survey sees markup, stylesheets and schema, reports repository tooling, and
  no longer contradicts itself about tests. A survey of a real web product
  reported no HTML, no CSS and no SQL for a repository that ships a static site
  and ten database migrations; reported no repository tooling for one with a
  Makefile, CI workflows and a lockfile; and printed "no test command could be
  resolved" directly beneath six modules whose test commands it had just
  confirmed. The language table now covers `.html`, `.css` and `.sql`;
  repository-level build files, CI configuration and lockfiles are reported as
  tooling; and the test-command summary names the scope it speaks for, so a
  repository with per-module commands and none at its root says exactly that.
  A file already identified as a manifest is no longer also counted as a
  language, which closes the `pubspec.yaml`-counted-as-`yaml` gap the corpus had
  been recording honestly as a precision loss rather than hiding.

- A validator can index a directory of documents in one call. A judge that must
  reason about a project's recorded decisions was discovering what those
  decisions are by reading them: across 97 runs on a real project the
  architecture validator took a median of 14 tool turns and a maximum of 44,
  opening records one after another because 68 accepted decision records at
  480KB do not fit an 8,000-token budget. It had begun to cost more than time —
  on one task the judge read eight records and ten source files, reached its
  conclusion, and then answered in prose instead of calling `submit_verdict`,
  an abstention that halts the task under the default policy. Two different
  judging models did the same thing at the same depth, so the cause was the
  length of the loop rather than the model. `summarize_directory` returns one
  compact record per document — path, title, and the leading key-and-value lines
  before the first subheading — computed from the files at call time, so there
  is nothing to maintain and nothing that can go stale. It reads the convention
  real documents use rather than assuming YAML front matter, which most decision
  records do not have. The engine learns nothing about decision records: which
  directory to index is the protocol's business, and what `Status` or
  `Supersedes` mean stays in the validator's criteria. The tool set stays
  closed, because a validator that can only read cannot mutate.

- `snodo models --stats` reports what the models on this project have actually
  done. Every LLM call was already recorded — model, tokens, cost, duration and
  the role that made it — and nothing showed an operator what those records
  said. Per model: calls, output tokens, the rate they came out at, the mean
  duration of a call, cost, and the roles served. Per coder: jobs, how many
  reached completion, and how long a typical one took. Two things it refuses to
  do. A model whose provider publishes no price is reported as unpriced, never
  as free, and an absent cost is never summed as a zero — the cheapest-looking
  model would otherwise be the one nobody has priced. And a figure resting on
  few observations says so, because a hundred percent across five runs is not a
  rate. Where a cost can be derived from the catalog it is labelled as an
  estimate and kept apart from measured spend.

- A long call says what it is doing. An orchestrator driving snodo over MCP had
  no way to tell work in progress from a server that had died, so it reported a
  timeout on every plan run and invented its own polling loop — while the engine
  knew exactly what was happening and could not say. `validate_task` now emits
  progress notifications when the caller supplies a progress token, carrying the
  narration the validators already produce. Nothing is emitted when no token was
  supplied, bodies are sanitised and single-line, and a caller that ignores
  every notification receives the identical response. `run_plan` is deliberately
  not included: it returns a job id in a second and has nothing to narrate.

- The dashboard shows plans, and a row wears its status. Tasks dispatched under
  a plan appeared as unrelated job rows with nothing saying which plan they
  belonged to or which wave they were. There is now a plans panel — each plan's
  waves and dependencies, every task with the status `status.json` records, and
  for a running task the job carrying it — and a job that belongs to a plan says
  so where it is listed. Rows are coloured by status: alive green, finished
  dimmed, failed orange, blocked or escalated red, with the status word always
  staying in its column so colour is never the only carrier. The panel remains
  an observer and offers no action.

- A verdict is bought once for a given question. Every recovery attempt was
  re-buying every verdict, including the unanimous ones about something that had
  not changed: on one real task, five validators ran twice against a
  specification that never moved. A verdict is now cached against what it
  judged — the validator, the exact text of its criteria, the model, the
  protocol, the phase and the subject — where a pre-execute single-completion
  judge keys on the specification and any judge that reads the tree, or any
  post-execute judge at all, keys on the work. That last part is the one that
  matters: a post-execute judgement keyed on an unchanged spec would reuse
  attempt one's verdict about code attempt two had rewritten, and because the
  prose would be byte-identical the loop would then halt as stalled on a
  judgement nobody made. Abstentions, errors and skipped passes are never
  stored, because none of them is a verdict. A reused verdict says so in the run
  output and in the audit trail, and a cold or unwritable cache behaves exactly
  as the engine behaved before it existed.

- A running coder is visible while it runs. The subprocess adapter captured both
  pipes and then blocked on a single call, so everything a coder narrated sat
  unread until it exited: a fifty-eight minute run showed one line and then
  nothing. Both pipes are now drained concurrently on reader threads — which is
  what the blocking call was doing for a reason, since a process writing heavily
  to one pipe while the reader waits on the other will deadlock — and each line
  is forwarded to the caller's progress sink as it is read. Foreground runs see
  the narration on the terminal; a background job's goes to its log, where the
  dashboard and `snodo job logs --watch` already tail it. Timeout still kills
  the process group and still raises with whatever output had been produced.

### Fixed

- `run_plan` starts a plan run as a background job and returns its job id
  immediately, instead of blocking until the whole wave finishes. A wave takes
  five to forty minutes and an MCP call caps at 180 seconds, so every real run
  timed out while the run itself carried on — the caller was told the call
  failed and had to infer from `list_jobs` that work was progressing. A plan
  run is now a job like any other: it travels the same wrapper, is followed
  with `get_job_status` / `list_jobs` / `get_job_logs`, gets the same liveness
  reconciliation for a process that died without reporting, reaches the same
  terminal statuses and archiving, and writes the same log the dashboard and
  `snodo job logs --watch` already tail. A plan-run job spawns child task jobs,
  so a listing must not blur parent and children: a plan row names its plan and
  leaves `task_ref` empty, while a child task row names the plan-run job in
  `parent_job`. `run_plan` still refuses a malformed plan synchronously, before
  anything spawns; the per-task validator quorum and the token each dispatched
  task consumes at its own boundary are unchanged; `get_plan` is unchanged; and
  `retry_job` / `snodo job retry` refuse a plan-run job rather than re-running
  a whole plan as though it were one task. The ability to block stays, but as
  an explicit `wait=true` (with an optional `timeout`) that a test or script
  opts into — never what a plain call does. (Fixes #254)

- A recovery attempt is now told it resumes from partial work instead of
  being handed the whole task again. A recovery subtask's spec carried the
  original intent verbatim plus the accumulated failures, framed as "implement
  the intent" — so a coder whose predecessor had satisfied four of five
  criteria re-established everything, re-ran the diagnosis it had already
  completed, and reproduced work that was already committed. Observed on a real
  run: a single unmet acceptance clause produced a recovery attempt whose net
  change was one test file, after it wrote and ran four probe scripts to
  re-derive a root cause the earlier attempt had already found, fixed and
  committed. The spec now states that the prior attempt's work is on disk in
  the worktree and must not be reproduced, names the validators that passed as
  the settled part, and carries each warning judge's own justification verbatim
  as the prior attempt's outstanding verdict — "four of five criteria hold and
  criterion 2 does not" is stated by the judge, not inferred from silence. No
  per-criterion positive is derived: `cited_criteria` means "criteria the judge
  mentioned" (it is scraped from the justification, including a bare-number
  fallback) and a passing judge can cite criteria too, so treating an uncited
  criterion as settled would turn a judge's silence into evidence. The spec
  states that the coder's job is the remaining work. The settled items are
  framed as "already holds, and will be re-judged"
  rather than "ignore the rest": validators judge the final state regardless,
  and a coder told the rest does not matter could still break it. The original
  intent is still carried verbatim, exactly once, and stays authoritative —
  nothing truncates, replaces, or rewrites it. The recovery depth cap,
  stalled-verdict detection, the halt taxonomy, warn/blocker classification,
  post-execute validation, and per-attempt token issuance are unchanged. The
  pre-execute quorum still runs in full: a recovery spec is new content (the
  accumulated failures and the settled summary change every attempt) that
  validators receive in place of the task spec, and the WF1 token gate is
  satisfied at dispatch from that quorum's results, so skipping it or reusing a
  prior token would weaken both the enforcement claim and the single-use token
  boundary (ADR 021, ADR 013).

- A failure's reason no longer dies at the handler that caught it. The engine
  had a recurring defect class — an exception is caught, a safe value is
  returned, and the cause is destroyed at the only point where it existed —
  costing an hour of operator time per instance (a provider rejection read as
  a bare `validator_error`, a `git ls-tree` that could not spawn reported as
  a BLOCKER "path not committed", a recovery probe's git failure misattributed
  as `no_file_operations`, a canary reporting a missing `ruff` binary as
  "failed to detect a lint violation"). The handlers on debugging paths —
  validator dispatch, the LLM and protocol-adherence validators' fault paths,
  the LiteLLM coder's response parsing, the in-place adapter's HEAD anchor,
  the container availability probes, the executor's work-recovery git probes,
  the readiness git checks and the dashboard's state readers — now log the
  caught exception's type and message before returning the safe value, and
  where the failure surfaces to an operator the reason travels into the
  surfaced message (the halt justification, the readiness finding). Readiness
  distinguishes "git could not answer" (a named tool-failure finding listing
  the reasons) from "git answered no"; the verification canary distinguishes
  a ruff that exited 1 (a verdict) from a ruff that exited 2 or could not
  spawn (a broken tool, reported as such). Handlers off debugging paths — telemetry
  extraction, temp-file cleanup, and the deliberate control-flow probes whose
  negative answer is the point — were left alone. No handling was removed, no
  crash was introduced, and the halt taxonomy and abstention representation
  are unchanged.

- Provider headers are resolved from one string by one mechanism. Two
  independent mechanisms had been added days apart for the same defect: one
  resolving headers inside the LLM validator at each call site, the other
  wrapping the completion function in `run_validators` so that every call would
  receive them by construction. Both were in the tree, and they resolved the
  provider from different strings. The per-call-site path used the model as
  written in the protocol; the wrapper used the model string in the call's
  kwargs, which the validator had already rewritten into litellm's routing form
  — `openai/<model>` for anything reached through the OpenAI-compatible driver.
  So for exactly the providers that need headers, a self-hosted or gateway
  provider whose config block is not named after its litellm provider, the
  wrapper looked up the wrong block and injected nothing. It never showed,
  because the wrapper declines to inject when headers are already present and
  the per-call-site path had put them there: the guarantee was never exercised
  on the path that motivated it, and the obvious cleanup — deleting the
  duplicate — would have silently reintroduced the original defect. The
  configured model name now travels with the call, the wrapper resolves from it,
  and the duplicate resolution is gone. A provider whose config block name
  differs from its litellm provider name is now a test case rather than an
  assumption. That consolidation also briefly made the no-tools completion
  paths pass an explicit `model`, overriding the model and `api_base` already
  bound together on the completion function (#237) and sending litellm a
  provider name it has never heard of; those paths carry only the configured
  name again, which the header wrapper consumes and never forwards.

- An environment fault is no longer a verdict about the task. A coder binary
  that is not installed was reported as a blocker: on a real project a
  specification passed every pre-execute validator unanimously and the run then
  died at execute with "opencode not found on PATH", and the plan recorded that
  task as blocked. The distinction existed in the code and was destroyed one
  layer below where it was made — the executor deliberately re-raised the fault
  as `execution_error` so it would not be laundered into an engine defect, and
  the canonical map then flattened `execution_error` to `blocker`. Three things
  followed: the status an operator read was wrong, the fix hints pointed at the
  specification and the code when the fix was an install command, and a recovery
  attempt would have handed a coder a specification that was fine and asked it
  to repair code that was never written. There is now a fifth canonical outcome,
  `environment_error`, recorded in ADR 015 along with why it is none of the four
  and what a consumer written against four should do with it — treat it as an
  operational halt, as it would `internal_error`, never as a blocker. The MCP
  validation contract still returns four outcomes; the fifth is execution-only.
  Dispatch now also checks that the configured coder can actually be invoked, in
  the process that will invoke it, before a task is dispatched — the readiness
  check for this already existed but ran in the operator's shell rather than in
  the server's.

- The cockpit tells the truth about plans, silence and cost. A job's plan label
  was derived from what a plan's `status.json` recorded, and that file does not
  list every task a plan ran, so two tasks dispatched minutes apart under one
  plan appeared one labelled and one not; membership now comes from the plan's
  own definition of its tasks. The "Last" column was red on every row — the
  colour marks a long silence, which may mean a dead run, but it fired for
  settled jobs too, and a job that finished four days ago has a long silence by
  definition; only a run that is supposed to be alive is coloured now, so the
  warning keeps its meaning and stops spending the one colour reserved for real
  trouble. The cost line reported a total across N runs, which does not say what
  the spend bought: it now splits completed against failed spend with the ratio
  between them, and names the runs whose cost was never recorded rather than
  counting them as zero. And the live log can be copied, so a failure can be
  pasted into an issue instead of screenshotted.

- Generated output is not source. Once markup became a language, a rendered
  coverage report made modules report languages they do not contain: one module
  reported HTML and CSS of which every file was in its `coverage/` directory,
  another two thirds. The rule is now named rather than the incident patched —
  a directory whose contents a tool generated from other inputs is output, so
  coverage and test reports, generated sites and the other conventional output
  trees are pruned alongside the dependency and build trees they resemble, while
  hand-written markup elsewhere is still source. Separately, a manifest-less
  directory of hand-written pages with its own test suite had appeared nowhere,
  because candidates were proposed on file count alone and three pages never
  reached the threshold: a directory carrying its own tests is a work in its own
  right and is now offered for judgement however few files it holds. Candidates
  are still never promoted on arithmetic, only by judgement.

- Validator progress reaches the operator, and a broken observer cannot stop a
  run. The LLM validator has always emitted a line per tool turn, and not one of
  them ever arrived: the context field carrying it held the engine's
  per-validator verdict handler, which takes two arguments, so every turn line
  raised a TypeError that was caught three lines below and written to a debug
  log. One field held two callbacks with different shapes, and the mismatch was
  swallowed. Narration and verdicts now have separate fields, every validator
  reports starting and finishing so an operator can see which of several running
  concurrently is still out, and both sinks are wrapped so that an observer that
  fails is reported once and then suppressed rather than taking down the work it
  was watching.

---

## [0.8.3] — 2026-09-12

### Added

- `snodo survey` reads an existing repository and reports the governance it
  already has, so a retrofit starts from evidence rather than from a
  questionnaire. It reports module boundaries, the languages each module
  actually ships, the test command each module can be verified with, where
  decision records live, and repository-level tooling. Two things it
  deliberately does not do: it never writes — no `.snodo/`, no
  `protocol.yml`, no decision records — and it never infers a requirement
  from an absence, so a repository with no tests is reported as having no
  test command, not as needing one. Arithmetic and judgement are separated.
  The deterministic pass reads manifests and counts files: it recognises
  `.svelte`, `.astro`, `.swift` and `.dart`, treats `pubspec.yaml` as a
  manifest, prunes `Pods/` and `Carthage/` the way it prunes `node_modules/`
  so a vendored tree contributes neither a language nor a boundary, and
  refuses to confirm npm's `echo "Error: no test specified" && exit 1`
  scaffold as a test command. Source-dense directories with no manifest it
  parses are *proposed* as candidates with their evidence, never promoted on
  their own. Judgement is routed to the configured agent, which is asked to
  classify rather than explore: is this manifest a work product or
  scaffolding, is this candidate a boundary. A verdict is accepted only when
  it cites files that exist inside the subject's own source, so every
  conclusion is attributable to evidence a reader can go and disagree with
  file by file; a model that cannot decide abstains, and abstentions are
  reported rather than resolved. Without an agent — none configured, the
  call failed, or the verdict was unverifiable — the deterministic result
  stands and the output lists which judgements were not made and why; a
  deferred gate is never an absent one. `--agent` / `--no-agent` force the
  choice. Measured on eight real repositories against hand-built ground
  truth fixed before the run (21 product module boundaries, 36 language
  occurrences in the repositories' own source): module-boundary recall 90%
  to 100%, precision 81% to 88% deterministic and 100% with judgement,
  language recall 67% to 100% with no vendored tree contributing anything.
  The module count reported is now the count the judgements concluded, not
  the count before they were applied.

- Planning is on the MCP tool surface, not only on the terminal. A consumer
  could drive the task loop end to end but could not reach the step above
  it: nothing exposed plan. An intent can now become a proposed plan
  (`propose_plan`), the plan is retrievable by its stable name at any time
  including mid-run (`get_plan`), it can be validated without executing
  anything (`validate_plan`, now always exposed on the all-modes server),
  and an approved plan can be run (`run_plan`). Returned shapes carry the
  plan's own structure — waves with ids, `depends_on`, tasks, statuses from
  `status.json` — never a terminal rendering, so the CLI's output text does
  not become a wire format. The files under `.snodo/plans/` stay the source
  of truth and nothing caches plan state. `run_plan` refuses a plan that
  fails the same `verify_plan_dir` the CLI gates on, before anything spawns,
  and the run itself is the CLI's plan-run path in a subprocess, so the
  engine's validators keep authority over every dispatched task and no route
  opens around them. WF1 holds: `propose_plan` and `run_plan` require a
  validation token and `run_plan` consumes it at the run boundary, as
  `dispatch_task` does. Mode-pinned agent servers stay capability-filtered;
  only the all-modes consumer surface always exposes planning, and the
  greenfield `plan` mode gains it through `MODE_TOOL_MAP`. The task loop is
  untouched. Refs #87.

- Every operator-facing LLM knob can be set from the command line instead of
  by hand-editing YAML. `snodo config set llm.<key>` now covers the role
  models themselves (`coder.model`, `validator.model`, alongside the
  `classifier.model` that was already settable), `num_retries` at the top of
  the `llm` section, and the recon knobs (`recon.num_agents`,
  `recon.models`, given as a comma-separated list) — the settable set now
  mirrors the typed fields of `LlmConfig` rather than a list that had fallen
  behind it. A model value is deliberately not checked against the provider
  catalog, because local and self-hosted models are legitimate and appear in
  no catalog. Unknown keys print the valid set instead of only refusing, and
  naming a whole sub-section where a value is expected is refused as a
  mistake rather than answered with a serialized object. `validator_llm.model`
  joins the moved-key table pointing at `validator.model`, so an operator
  reaching for the old name is redirected rather than told it does not
  exist — on `get` as well as on `set`.

### Fixed

- Retrying a task no longer destroys the specification being retried, and the
  command snodo prints as a follow-up is now the command that does nothing to
  it. `snodo run --retry <task_id>` takes a positional description that
  *replaced* the task's spec, and the retry suggestion the CLI printed at three
  sites (`followup.task_retry`, the exhausted-retry guidance in `run_cmd` and
  `plan_run`) was `snodo run --retry <task_id> "revised spec"` — so pasting
  the tool's own advice overwrote the specification with the literal
  placeholder. On a real project this cost a sixty-line spec written for a task
  that had failed for an operational reason (a provider rejected the validator's
  request over a missing header); the spec was never implicated, two attempts
  later the meta-spec validator correctly called the task underspecified, and
  the run escalated on a specification that survived only inside old job
  payloads. Retry is now three distinguishable shapes: a bare `--retry` re-runs
  against the spec on record and is the only shape the CLI suggests;
  `--append-spec` (or the positional, which can no longer destroy anything)
  adds guidance on top of the spec and keeps it; `--replace-spec` replaces it,
  naming itself as the destructive act. A replaced spec is written to the
  task's failure record (`superseded_specs`, surfaced by `snodo task show`, and
  carried forward when the engine rewrites that record on a later attempt) and
  audited as `spec_replaced`, so the operator's copy outlives the command they
  were told to run. Contradictory combinations are refused rather than guessed
  at. `snodo job retry` and the MCP `retry_job` tool take the same three shapes
  so no surface prints or accepts a destructive default; a resumed background
  plan task now declares its spec with `--replace-spec` instead of relying on
  the positional's old meaning. Branches, worktrees, failure context and
  validator behaviour are unchanged — a genuinely underspecified task is still
  caught, and the retry prompt still opens with `Original spec:`, which is what
  the task branch name is derived from.

- A tunnelled MCP server rejected every correctly issued token. The bearer
  verifier called `jwt.decode` without an `audience` argument, on the
  understanding that omitting it skips the audience check. It does the
  opposite: PyJWT raises `InvalidAudienceError` when a token carries an `aud`
  claim and the caller named no audience (`api_jwt.py` `_validate_aud`). Every
  resource-indicated token carries one, so each was refused for being correct.
  The rejection was then swallowed into a bare `None` and surfaced as the MCP
  SDK's generic 401 "Authentication required" — a message that says a token
  was absent or malformed when in fact it was perfect. The verifier is now
  given the server's resource identifier and verifies `aud` against it, from
  the same string the protected-resource metadata advertises, so the two
  cannot drift. `AccessToken.resource` is populated from the claim, which a
  later SDK is expected to compare against `resource_server_url`. Every
  rejection reason is now logged at debug rather than collapsed: diagnosing
  this took a night because the reason existed and was discarded.

- Provider headers now reach every completion call by construction. Provider
  headers are task-scoped — opencode Go rejects a request without
  `x-opencode-session` — so unlike credentials they cannot be bound when the
  completion function is built; task identity only exists when
  `run_validators()` is called. The result was a call surface where some
  paths carried headers and some did not: the LLM validator's tool-loop path
  resolved them, its single-completion path did not, and neither path of
  `protocol_adherence` did. Observed on a real project: a task failed twice
  against a local provider with the console showing only that the validator
  errored, while the validators that happened to use tools passed — the
  failing one was `meta-spec`, which asks for a verdict and calls nothing,
  and the provider's own message, "Request is missing x-opencode-session",
  was reachable only by setting `LITELLM_LOG=DEBUG`. `run_validators()` now
  wraps the completion function in a closure over the task id that resolves
  and injects headers on every call, so a validator cannot forget to pass
  them and a new call site inherits header support without being modified.
  Header resolution stays at call time, which is what task-scoping requires.

- A judge that never returns a verdict now abstains rather than blocking.
  When an LLM validator was asked again for `submit_verdict` and still
  answered in prose — or did not answer — the validator returned a
  `blocker` with `error=True` and a justification saying no reliable verdict
  could be obtained. That is a fabricated fault: it halts the task on a
  finding the judge never made, and it reports the judge as having issued a
  verdict. Reaching no verdict is now recorded as what it is — severity
  `None`, an `abstention_reason` naming the shape of the failure, and the
  same `examined` / `unexamined_tools` fields the turn-cap path already
  records — and the protocol's abstention policy decides the consequence.
  Since that policy defaults to blocking, a protocol that wants to halt
  still halts; the difference is that the halt is attributed correctly and a
  protocol that would rather proceed on an abstention now can.

- The job listing is bounded and each row names the work it belongs to.
  `list_jobs` returned every job's full task description, so a listing's
  cost scaled with spec prose across all of history and the MCP tool
  returned specs to a consumer that had only asked what was running. A row
  is now a bounded summary — id, status, the task it belongs to
  (`task_ref`), a one-line title clipped to 120 characters, exit code,
  created, started and completed times, and duration — and the spec itself
  stays where it belongs, in `get_status`. The title is the first line that
  reads like prose rather than the first line outright, because specs are
  templates whose first line is a section label; taking the first line
  literally would have printed `INTENT` for every row. The terminal listing
  shows exit code and duration alongside, so a run that ended badly is
  visible without inspecting it.

---

## [0.8.2] — 2026-09-09

### Fixed

- A tunnel conflict is now something an operator can act on. The cloud
  allows one tunnel per project and mode and refuses a second, naming the
  hostname that blocks it — a fact the CLI had in hand and discarded,
  reporting the refusal as a flat failure. Worse, `--delete` consulted only
  the local project's configuration, so a tunnel that existed for the
  organisation but was never recorded locally was invisible to it: the two
  commands contradicted each other and neither could resolve the state. The
  blocking hostname is now surfaced with the commands that clear it, and a
  tunnel can be deleted by the identity the cloud holds rather than only the
  one the local config remembers. A conflict never reuses or silently
  replaces the existing tunnel: the refusal carries no tunnel token, so
  there is nothing to run against, and deprovisioning something another
  session may be using stays a deliberate act.
- A config directory is no longer mistaken for a project. Project resolution
  walks up from the working directory looking for `.snodo` and excludes the
  global config directory, but the exclusion only covered whichever home was
  currently configured. With `SNODO_HOME` pointed elsewhere, a real
  `~/.snodo` on disk stopped being excluded and the walk reported the user's
  home directory as the project root — silently, so a run proceeded against
  the config directory as though it were a repository. The machine's own
  home is now resolved independently of the environment, since `$HOME` is
  exactly what a redirected process cannot be asked.
- The test suite no longer depends on the machine it runs on. Six plan-CLI
  tests passed only where someone had already run `snodo init`: the signing
  key directory was resolved once at import time from the real home
  directory, so a clean checkout failed with "Public key not found at
  ~/.ssh/NO-AGENT/snodo.pub.pem" and the release job went red while the same
  command reported 3332 passed locally. Key paths now resolve per call, the
  production location and its deterrence rationale unchanged, and the suite
  redirects HOME and git's global config to a private per-session directory
  with a throwaway keypair seeded into it. A tripwire fails the session if
  anything touches the real `~/.ssh/NO-AGENT`. Two further couplings were
  found and closed on the way: 41 tests inherited `init.defaultBranch` and a
  git identity from the developer's `~/.gitconfig`, and two init tests were
  regenerating the developer's real private key on every full run. The
  engine still refuses to build a graph without a signing key, now covered
  explicitly rather than by accident.
- A follow-up command now matches the state of the thing it names. Every id
  the CLI printed was accompanied by `snodo task show <id>`, which reads a
  halt or failure record — a running task has neither, so the suggestion was
  printed at dispatch and answered "No record for task ..." for exactly as
  long as the task was alive, then started working once the operator no
  longer needed it. A running task is now pointed at the dashboard and a
  finished one at its record; a job gets `snodo logs <id> --watch` while it
  runs and `snodo job status <id>` once it stops. `snodo task show` also
  distinguishes a task that is still running from an id it has never heard
  of, which read identically before.
- `snodo serve --tunnel` reached the wrong service. Tunnel provisioning and
  audit sync were built from one configuration key, so a provision request
  inherited the ingest host and was answered by the ingest worker, which
  validates every request it receives as an event batch — hence the
  "Missing or invalid field: project_path" for a field no tunnel API has.
  The two services are now configured independently, with a config written
  before the split resolving to the tunnel default rather than the ingest
  host. Provisioning failures also report what the response actually was
  instead of advising that the API key may have expired, which was the
  wrong suspect for any error that never reached an authenticator.

---

## [0.8.1] — 2026-09-09

### Changed

- A validator that exhausts its tool-turn budget without deciding is now
  recorded as an abstention rather than an engine fault, and an abstention can
  no longer be mistaken for agreement. Previously an exhausted judge produced
  `validator_error` and failed closed, killing a task the other validators had
  passed; the turn count grows with the repository and the budget does not, so
  this became routine on larger projects. Judges are now told how much of their
  budget remains so they can return a verdict before it runs out. Token
  issuance refuses to mint a validation token when any validator abstained and
  excludes abstentions from the token's signature list, so a judge that never
  decided can never be signed into a token as having passed. Run output marks
  an abstention distinctly instead of showing a pass tick, and the audit trail
  records it as an abstention with its reason. Protocols gain
  `abstention_policy`, defaulting to `blocking` so protocols written before
  abstentions existed keep failing safe. The absence of a verdict is carried in
  the model rather than beside it: `ValidatorResult.severity` is now optional
  and is `None` when no verdict was reached, with `abstention_reason` saying
  why. A consumer reading a severity therefore cannot mistake an abstention for
  a verdict, and no per-caller guard is needed to avoid it. Anything
  constructing or reading a `ValidatorResult` should expect `severity` to be
  `None`. (Fixes #244, Fixes #245, Fixes #249)
- The cockpit's Tasks and Jobs panes now say *when* a record ran, not only
  whether it is alive. Thirty completed tasks used to read as one undifferentiated
  block: the run from an hour ago looked exactly like the run from last week, no
  row showed how long it took, and rows came in whatever order the plan
  directories were walked, so the task that just finished could sit twenty rows
  down. Each row now carries **Started** (a wall-clock stamp, dated once it is
  older than a day), **Ran** (start to the last sign of life, or to now for a
  run still going) and **Last** (when the record last moved, coloured by how
  long it has been silent), and both panes lead with the most recently started
  work. Descendants stay under their parent — the ordering is applied level by
  level, never to a flattened list — and a record that never wrote a
  `started_at` still renders, sorting last rather than crashing or vanishing.
  The pane fits eight columns, so three new facts displaced three old ones:
  **Phase For** and **Idle** are the same number for any finished record, and a
  duration says more than either once the run is over; **Alive?** goes because
  its fact survives in **Status**, which now renames a silent run `stale`, dims
  a settled one, and appends `pid not recorded` when liveness cannot be
  answered at all. Nothing new is recorded: every fact comes from the
  `started_at` and the audit/usage markers already on disk, the panes stay pure
  readers (no telemetry, no new audit events or state fields, no lock, no
  write), and reading the whole settled set costs the same single bounded pass
  a refresh already made.

### Added
- A wave is now openable from the cockpit. Pressing `w` on a selected task
  opens the wave that owns it, showing the wave's description, the anchor
  summaries of the separate intents folded into it, every member task with its
  current state, and when the wave was created and last moved. Waves carry a
  name and a description that group and explain a set of tasks, which was
  visible nowhere: the cockpit's Wave column names the wave but says nothing
  about it. This is a drill-down, not a navigation level — the cockpit keeps
  its selection and is restored to it on close — and it reads only, in keeping
  with the dashboard being an observer. (Fixes #253)
- The dashboard's Tasks and Jobs panes now answer the operator's real question — is this
  still alive, and how long has it been in the phase it is in — from what the engine already
  records. Each row shows the current phase, how long it has been in it, seconds since the
  last sign of life (audit event or LLM-call usage record, the number that matters most and
  was previously unavailable anywhere), whether the process is alive, and cost so far. A
  foreground run now records its pid in `.snodo/tasks/<id>/state.json` at start, the same
  way a background job's wrapper already does, so liveness is answerable for both. The panes
  are pure readers: no telemetry, no new audit events, no new payload fields, no lock on the
  audit log or any state file (the engine appends under `fcntl.flock` while the dashboard
  reads), and a partially written state file or torn audit tail is tolerated rather than
  crashing the screen. A record whose state file still says `running` but whose pid is dead
  and whose last sign of life is older than ten minutes is named `stale` rather than
  presented as live — the active session's `current_task` is deliberately not a liveness
  signal, because it is set at run start and never cleared, so it is exactly as stale-prone
  as the `status: running` field it would be asked to rescue. In-place subprocess coders
  record no per-turn usage (ADR 034 documents that as a decision, not a gap), so during the
  coder phase the pane says `blind` and leans on elapsed time rather than implying progress
  it cannot see. (Fixes #236)
- The cockpit's upper-left pane is now **Needs You**, replacing the Sessions
  pane. The Sessions pane earned its half-height only when there were many
  sessions — not the normal case — and its one fact (which session is active)
  already prints in the header, so the header now carries it and the pane is
  gone. The reclaimed space shows the only thing on the screen that will not
  move until a person acts: tasks that escalated or halted on a judge that
  could not reach a verdict, each with what it awaits and how long it has been
  waiting — previously discoverable only by remembering to run `snodo authorize`.
  Below that, halts are grouped by their recorded outcome (a wave that failed
  the same way six times reads as one fact, not six) and a project cost rollup
  aggregates the per-call usage records the engine already writes. When nothing
  awaits, the pane says so plainly rather than leaving an empty frame.
- The cockpit gains one search (`/`, cycle with `n`) that finds a task, a job or
  an audit event by text from one place and moves the operator to the match. It
  reads only the settled task and job records and the bounded audit tail already
  in the refresh's snapshot; it never reads a job's stdout/stderr — where the
  megabytes are — so it restores none of the cost the bounded log tail removed.
  Searching job output is a separate, explicit, opt-in command (`:output <text>`)
  that warns it is slower and still reads only a bounded tail per job. The dashboard
  remains an observer: no dispatch, plan, recon or mutation, no audit event
  written, no lock taken, no timer — exactly one read on mount and one per
  explicit refresh, as before. (Fixes #251)


### Fixed

- The cockpit no longer slows down as a project grows. Every keystroke rebuilt
  everything: `providers._get_audit_log()` constructed an `AuditLog`, whose
  `__init__` parsed and hash-chain verified every event in the file, and
  `get_all_events` then threw all but the last twenty away; `get_job_log` read a
  job's entire stdout with `read_text()`. Both ran from `_cascade_update`, so the
  cost of moving the cursor one row scaled with the size of the audit log and the
  length of a job's output, neither of which is bounded. The viewer now reads what
  it displays: the audit tail reads a bounded window at the end of the log through
  the existing lock-free `tail_audit_events`, a job log reads a bounded tail, and
  the settled Tasks and Jobs panes are re-read only when the session changes (or on
  an explicit refresh), never as the operator moves inside them. The hash chain is
  verified by `snodo audit verify`, where it is a governance claim; re-verifying
  2,698 events on every keypress was a performance bug wearing a safety property.
  The Jobs pane now lists every job the project has with the owning task shown as a
  column rather than only the selected task's jobs, because a job's identity
  includes its task and that is a column, not a filter. And the Live Log no longer
  prints raw ANSI escapes literally — job stdout is decoded into styled text rather
  than parsed as Rich markup. Textual is unchanged, no timer returns, no lock is
  taken on the audit log or any state file, and no governance verification is
  weakened. (Fixes #247)
- An abstention — a tool-loop validator that exhausted its turn budget without reaching a
  verdict — is now recorded, adjudicated, checkpointed, and displayed as the absence of a
  verdict rather than as a pass. The representation (severity absent, not severity `pass`
  behind a flag) carries the truth through every site at once: the `validate` and
  `validator_results` audit events, the graph-state checkpoint (an abstention round-trips
  through `_state_to_dict`/`_dict_to_state` and is recoverable; a stored result without a
  severity is no longer restored as `pass`), the halt payload and escalation payloads, and
  the engine's own retry evidence (`task_failure.failed_validators` now carries the silent
  judge with a "could not decide" annotation). The adjudication path finally sees the case
  it exists for: pending-decision writers in the engine and the MCP server create
  adjudicable entries for abstentions carrying which judge abstained, why it ran out, and
  what it did and did not examine; `snodo authorize` renders that detail and mints a
  human-signed record with `adjudicated_severity: "abstain"`, which the policy evaluator
  honours by retiring that judge from the quorum — never converting the silence into a
  pass vote. A halt caused by abstentions names the abstainers instead of claiming
  blockers that do not exist, CLI/MCP instructions distinguish "no blockers; N validators
  abstained" from "blockers present", the dashboard shows `◐` rather than a green tick,
  and a severity_cap can no longer rewrite an abstention into a capped verdict. Policy
  decision behaviour, the halt taxonomy, the fail-closed error path, and token issuance
  (which already refuses to sign an abstention) are unchanged. (Fixes #252)

- The cockpit dashboard and all other dashboard screens now refresh only when the
  operator presses the refresh key (`r`) instead of continuously rewriting every
  cell on a 2-second interval. The automatic timer is removed. Each screen now shows
  when the displayed data was last read (`Data: 5s ago`, `Data: 2m ago`), so the
  operator can distinguish a quiet system from a stale view. The data-age indicator
  is calculated at header update time with no separate timer. Refresh no longer
  moves the cursor: all `_programmatic_move` flag set/clear pairs are wrapped in
  `try/finally` so the flag is always cleared even if exceptions occur. Audit log
  read failures are reported plainly instead of being swallowed. (Fixes #240)
- The in-place coder attribution tests no longer poison other libraries' lazy
  imports. They patched `subprocess.Popen` module-globally to fake the host
  CLI, which captured every other caller in the process: GitPython runs
  `git version` through `Popen` during its own initialisation, and the
  adapter's `implement()` imports git lazily, so the first adapter test to
  run received the fake's bytes response and raised `ImportError: Failed to
  initialize: endswith first arg must be str ... not bytes` from GitPython's
  import. Three tests failed as a result; the two that do not use an adapter
  subprocess passed. The fakes now target the adapter's own
  `_run_subprocess` call site (the seam the sibling coder tests already use),
  so the patch reaches no further than the subprocess call the test is
  actually exercising. A guard re-drives the two adapter tests with the
  recording call neutralised and asserts they FAIL, so a future regression in
  the attribution path is a red suite rather than a silent pass. (Fixes #243)
- The cockpit dashboard hierarchy changed to show waves as labels (a column on each
  task row) rather than filter levels. The spine is now Session → Tasks → Jobs
  instead of Session → Waves → Tasks. All tasks from a session are shown regardless
  of wave, so the Tasks pane is never empty because the oldest wave has no current
  tasks. The layout was reduced from three horizontal panes to two, which fits a
  standard terminal width. The three-pane structure (Sessions, Waves, Tasks) forced
  choice between wide viewing and fitting the screen. (Fixes #239)
- `snodo models` now resolves cost and context metadata for providers the
  operator configured themselves, even when the block name differs from the
  catalog's provider key. Two independent bugs in `_normalise` in
  `snodo/infrastructure/model_catalog.py` stopped the lookup: the configured
  provider's own prefix was only stripped for three providers (cloudflare,
  google, deepseek — special only because their prefixes differ from their
  block names), so `ollama/deepseek-v4-flash:0731` was looked up as
  `models["ollama/deepseek-v4-flash:0731"]` instead of
  `models["deepseek-v4-flash:0731"]`; and the block's name was used as the
  catalog's provider key, two different vocabularies (the catalog carries 213
  providers and names this one `ollama-cloud`; the operator's block is called
  `ollama`). Stripping the provider's own prefix is now the general rule, and
  a provider block can declare `catalog_provider` to say which catalog key it
  corresponds to, defaulting to its own name so every existing configuration
  keeps working unchanged. A model id containing a colon or a slash survives
  normalisation untouched. Cost that the catalog states as `None` (e.g. a
  subscription plan) is still reported honestly as unknown; the context window
  is present and appears. No new per-provider branches, no hardcoded mapping
  table, no change to what is printed when metadata is genuinely absent, and
  the catalog is fetched no more often than the existing 24h cache TTL.
  (Fixes #241)
- `snodo models` now prints every number in the unit it was published in, and
  says which kind of absent a missing value is. The cloudflare branch of
  `_normalise` stripped `openai/@cf/` but the catalog key keeps the `@cf/`
  (only `openai/` is snodo's prefix), so `openai/@cf/google/gemma-4-26b-a4b-it`
  never resolved; it now resolves to `@cf/google/gemma-4-26b-a4b-it` and its
  cost and context come from the catalog. A model id carrying a `:tag` now
  resolves when the catalog carries the model untagged (the tag is stripped
  only as a fallback, so a tagged catalog entry still wins). A published price
  renders at its true magnitude: models.dev publishes dollars per million
  tokens and the litellm fallback publishes dollars per token, and the lookup
  result now carries `cost_unit` so the caller never scales by the wrong
  factor — the previous code multiplied every price by 1,000,000, printing
  `$100,000.00` for a `$0.10`/1M model. A context window renders in K/M
  (`1048576` → `1M`, `262144` → `256K`) while the exact value stays available
  on the lookup result. `unknown` no longer means two things: a model that
  resolved but publishes no price prints `none published`, and a model that
  did not resolve at all prints `unknown` (the lookup result carries `found`).
  No hardcoded price table, no hardcoded provider-name mapping, no invented
  cost where the catalog states none, no business-model label the catalog does
  not state, and the 24h cache TTL is unchanged. (Fixes #242)

- A role's model and credential now travel with its completion call instead of
  through process-global state. Every provider block declaring
  `litellm_provider: openai` had its key written into `OPENAI_API_KEY` in
  addition to its own `api_key_env`, the write was never undone, and the last
  role set up won for all of them — so two OpenAI-compatible providers in one
  run authenticated each other's calls and failed as an authentication error
  naming the wrong service. The classifier additionally passed its raw model
  string as a kwarg, overriding the bound completion function, so a configured
  provider block never became a litellm-shaped name and its `base_url` was
  never applied. `api_key` is now bound alongside `model` and `api_base`, and
  the classifier uses what the binding carries. (Fixes #237)
- Gave the consumed-token store's SQLite connection an explicit, bounded
  lifetime. `TokenStore` cached the connection opened in `_connect` with no
  way to release it — no close, no context manager — so every WAL-mode
  connection (three descriptors: database, `-wal`, `-shm`) leaked until the
  garbage collector reclaimed it with an "unclosed database" ResourceWarning,
  and `uv run pytest tests/ -m "" -n auto --cov` died partway with
  `OSError: [Errno 24] Too many open files`. `TokenStore` and `TokenIssuer`
  now provide `close()` and context-manager support (the lazy per-instance
  connection and its one-time WAL/DDL setup are unchanged), a `__del__` safety
  net closes connections reclaimed from owners that forgot, `GraphBuilder`
  tracks whether it constructed its `TokenIssuer` and releases only its own,
  and `run_cmd` now constructs the issuer, passes it into
  `build_protocol_graph`, and releases it at the same teardown points as the
  checkpointer. The checkpointer tests in `tests/infrastructure/test_memory.py`
  close their `SqliteSaver` connection in `finally`, honouring the documented
  contract. (Fixes #234)
- A coder that stopped without writing now leaves enough behind to diagnose it.
  `SubprocessCoderAdapter` built its diagnostic tail as `out_tail or err_tail`
  on every exit path: stderr was used only when stdout was empty. A coder that
  narrates to stdout therefore lost its stderr entirely — which is exactly
  where a budget exhaustion, a rate limit or a provider error is reported. The
  observed case: two ~7-minute runs of the same task, each exiting 0 with no
  files written, each leaving ten preserved lines of the coder saying it was
  about to read another file, and nothing anywhere in the record saying why it
  stopped; the runs were indistinguishable because the discard was
  unconditional. Each stream is now tailed under its own budget, so what is
  kept includes the end of the run in BOTH channels, and the three exit paths
  (timeout, non-zero exit, zero exit with no changes) share one 2000-character
  per-stream limit where they previously disagreed (2000/2000/1000) for no
  stated reason. A halt caused by this shape (exit 0, no writes) is still
  `no_file_operations`: whether the coder decided no change was needed or
  never got as far as writing cannot be told without parsing coder-specific
  output, which ADR 034 keeps out of the adapter — but the record now carries
  the evidence for the operator to tell (a stdout closing explanation versus a
  stderr error), and the halt reason says to look at `output_tail`. Raw coder
  output still never enters the audit log or the wire: the writeback strip
  that keeps it out of session checkpoints is unchanged and still holds.

---

## [0.8.0] — 2026-09-07

### Added

- Added `snodo session new` to begin a new protocol session for the current project
  and mode without destroying or modifying the previous session. The active pointer
  moves to the new session while the outgoing session remains intact on disk, inspectable
  via `snodo session show` and listed in `snodo session list`. Warns and requires confirmation
  when the outgoing session contains live state (pending adjudication proposals or in-progress
  tasks), with `--yes` / `--force` to skip for scripted use. (Fixes #220)
- Added a deterministic method scaffolding readiness check (`snodo ready` / `snodo readiness`).
  Derives checks dynamically from the compiled protocol (committed decision records for architecture
  validators, resolvable test commands for quality validators, committed paths cited in criteria,
  committed coder configuration files). Reports ordered findings (cheapest fix at highest severity
  first) and a scored repository readiness percentage, while reporting environment-specific workstation
  prerequisites separately and unscored. Emits findings as a `readiness_checked` audit event following
  cloud payload discipline without mutating project state or changing validator execution behavior.
  (Fixes #216)

### Changed

- Every shipped template now carries a working default test command, so a fresh
  project initialised in a directory with no marker file for any supported stack
  no longer halts on its first task. `solo`, `team` and `2+n` previously shipped
  an empty `tooling` map — when auto-detection found nothing the quality
  validator halted with "No test command resolvable" — and `greenfield` shipped
  the literal `REPLACE_ME`, which reached the shell and exited 127. Templates now
  ship a POSIX no-op that prints a notice and exits zero; auto-detection and
  `snodo init --test-command` still take precedence over it. When the no-op runs,
  the quality validator records a `verification_executed` audit event with
  outcome `no_tests` (and states plainly that no tests were executed) instead of
  claiming a pass for work that did not run, the merge gate accepts that honest
  record so a fresh project's first task lands, and the ungated run is shown in
  the run output rather than only in the log. (Fixes #215)

### Fixed

- `unmerged` is now a terminal job status. The job wrapper writes it for exit
  code 2 — work that finished and verified but whose branch could not be merged
  — but `TERMINAL_STATUSES` did not contain it, and every caller that asks
  whether a job is done consults that set. An unmerged job therefore looked
  perpetually live: the concurrent plan runner's poll loop never removed it from
  the active set and spun until killed, `wait_for()` ran to its timeout,
  `archive_jobs` never reaped it, and `_reconcile_state` rewrote it as `failed`
  with `exit_code -1` and "Process died unexpectedly" once the process was gone
  — so completed-but-unmerged work was recorded as a crash. (Fixes #232)
- The plan layer reports the outcome the engine decided instead of collapsing it.
  `snodo run --plan` printed `FAILED` and recorded every halted task as `blocked`
  regardless of its canonical halt — so an escalate, a blocker, a validator that
  produced no verdict, and an engine fault were indistinguishable, and an
  operational fault was fed to the next attempt as a critique. The plan runner now
  reads the persisted halt payload (`halt_type` / `final_decision`) and names the
  outcome in the report (`BLOCKED`, `ESCALATED`, `VALIDATOR ERROR`,
  `INTERNAL ERROR`). It records `blocked` only for tasks the engine judged and
  failed (blocker/escalate) and a new `errored` status for `validator_error` /
  `internal_error` halts, which are not retried with failure context — the next
  attempt starts fresh. `plan_cmd.py` renders and counts `errored` tasks. The
  engine's classification and the audit log are unchanged. (Fixes #231)
- Distinguish complete-but-unmerged tasks from failed tasks and allow fast-path merge retry.
  When a task closure is resolved and verified at its commit but auto-merge fails due to
  a repository lock collision or conflict, report the task as `complete (unmerged)` and record
  status `unmerged` (in `.snodo/tasks/<task_id>/state.json`, background job status, and plan
  `status.json`) rather than `FAILED` / `blocked`. Subsequent attempts re-check the merge gate
  against the verified branch and perform a fast-path merge directly without re-running the coder.
  (Fixes #228)- Serialised repository merges and git ref/index updates across concurrent wave tasks.
  Added re-entrant `merge_lock` at `.snodo/.merge.lock` held across the entire merge
  operation (target commit resolution, gate check, git merge, HEAD SHA resolution,
  `task_merged` audit event recording, worktree setup/removal, and task branch deletion).
  Concurrent wave tasks racing to merge now wait their turn and merge cleanly into base
  rather than colliding on git repository locks (`.git/index.lock`, `.git/HEAD.lock`)
  and failing with `merge_failed_escalated`. (Fixes #226)
- Ignored and excluded repository-local snodo home and reported plaintext keys in readiness.
  When `SNODO_HOME` is pointed to a directory inside a repository, `snodo init` now ensures
  the repository-local home directory is added to `.gitignore` and committed (recognised by
  resolved path rather than name), and coder adapters strictly exclude the local home directory
  from staging and diff readback, while leaving coder mutation snapshotting confined to `.snodo/`
  to avoid false halts on snodo's own live database writes. In readiness checks, configured
  providers carrying plaintext `api_key` values while `api_key_env` is available are now reported
  in the unscored workstation category without modifying the repository score or altering configuration.
  (Fixes #227, Fixes #229)

- Extracted acceptance section for acceptance validator prompt.
  `AcceptanceValidator` now extracts delimited acceptance criteria sections
  (e.g. `## Acceptance Criteria`, bare uppercase `DONE WHEN`, or `Acceptance criteria:`)
  from the task spec, recognising subsequent section boundaries by heading shape (bare uppercase,
  colon-delimited, bold, or markdown headers) rather than a fixed vocabulary, passing only
  the relevant criteria to the judge prompt while falling back honestly to the full spec when no
  delimited section is found. Other validators (meta-spec, architecture, security) continue
  to receive the entire spec. (Fixes #224, Fixes #225)

- Announced project identity on session resume as well as session creation.
  `_resolve_session` in `run_cmd` now emits `project_announced` alongside `session_resumed`
  when adopting an existing active session or explicitly resuming via `--resume`, giving
  per-run announcement cadence so existing projects announce their identity on subsequent
  runs without changing the payload format or identity resolution. Updated `docs/specs/cloud-sync.md`
  to reflect the per-run cadence. (Fixes #219)

- Kept workstation findings out of the `readiness_checked` audit event payload.
  The audit payload transmits repository findings only and retains `workstation_findings_count`,
  preventing machine-specific binaries and environment variable names from entering the
  wire audit stream while leaving workstation findings in the operator terminal report.
  Documented `readiness_checked` in `docs/specs/cloud-sync.md` as the twenty-third event type.
  (Fixes #217)
- Configurable agy print timeout and preservation of committed changes on non-zero exit.
  `AGYAdapter` now sets `--print-timeout` in its subprocess argv based on the configured
  `timeout_seconds` (via `llm.coder.timeout_seconds` or mode-level `coder_config`), preventing
  runs from being cut off by agy's 5-minute default when snodo's budget is longer.
  `SubprocessCoderAdapter` now inspects the working tree for committed files on non-zero
  exit before raising `LLMCallError`, preserving completed work in the returned `CodeArtifact`
  and surfacing `output_tail` metadata. (Fixes #218)
- The recovered-work guard now reads the configured task-branch prefix instead
  of hardcoding `"task/"`. The protocol makes the prefix configurable
  (`execution.branch_prefix`, default `"task"`), so a project that changed it
  previously got no guard at all and a retry silently dropped committed work
  exactly as it did before the fix — a guard against silent data loss must not
  itself fail silently on a supported configuration. The `work_already_present`
  event is now documented in `docs/specs/cloud-sync.md` as the twenty-fourth
  transmitted event type, carrying `task_ref`, `base_ref`, `artifacts_count`,
  and `files` (repository-relative paths only). Other call sites that hardcode
  the same literal are out of scope. (Fixes #222)
- A retried task whose work is already on its branch is no longer reported as
  if the coder did nothing. When a task commits work and then fails afterwards,
  the retry runs on a task branch that already carries that commit; a coder that
  finds the work present correctly writes nothing, and the engine previously
  halted with `no_file_operations`. The engine now compares against the branch's
  base — the point where the task branch diverged from the branch it will merge
  into — and when the branch is already ahead of that base the work exists: it
  reports that plainly (`work_already_present`) and carries the existing
  artifacts into post-execute validation exactly as freshly produced ones would
  be, so the validators judge what is actually there. `no_file_operations` now
  means the work does not exist, not that this particular attempt did not create
  it; a coder that produced nothing on an unchanged branch is still
  `no_file_operations`. The merge gate is unchanged: work found this way faces
  the same verification requirement as work produced in the run. (Fixes #221)

- Kept coder closing output local and out of session checkpoint wire audit events.
  `_auto_write_halt_payload` strips `output_tail` before saving to the session checkpoint
  via `update_decision`, preventing coder stdout from being transmitted in `session_decision_updated`
  audit events while preserving the full output tail in local state files (`.snodo/tasks/<task_id>/state.json`,
  `.snodo/jobs/<job_id>/state.json`) and halt console output. `snodo task show` resolves `output_tail`
  from local state files. (Fixes #213)

- Preserved coder closing output on zero-file halt and distinct stream truncation.
  `SubprocessCoderAdapter` now keeps stdout and stderr distinct, truncates each on
  its own terms, and takes closing messages from stdout rather than whichever stream
  wrote last. A zero-file completion preserves the coder's explanation in the halt
  output, structured halt payload (`reason`, `output_tail`), and retry failure context
  (`task_failure`), while keeping raw coder output out of the tamper-evident audit
  log. The timeout branch receives the same stream distinction fix. (Fixes #212)

---

## [0.7.3] — 2026-09-02

### Added

- A project's identity now follows the repository. A repository with a git
  remote takes that remote's normalized URL, so every engineer working from it
  resolves the same identity on any machine; a repository without one takes a
  locally generated `local:<uuid>` that is deliberately unreconcilable, because
  nothing can establish that two local checkouts are the same project. The rule
  was cited in the code as ADR 012 for months without that record existing, and
  in its absence the implementation drifted: a cached `local:` identity
  short-circuited resolution, so a repository that gained a remote never
  promoted. Promotion is now one-way and immediate, the local uuid stays stable
  across runs rather than being re-minted, and `scope` is derived from the
  identity so the two cannot disagree. The decision is now written down.
  (Fixes #205, Refs #202)

- Cloud sync transmits `scope` alongside `project_id`. Its purpose is to tell a
  consumer when *not* to reconcile: a `remote` identity is the same project
  everywhere, a `local` one is a leaf and must never be merged with anything,
  however many clones report the same display name. (Refs #205)

- The halt audit event now records the canonical outcome and the specific halt
  type. The four-outcome taxonomy — escalate, blocker, validator_error,
  internal_error — was the central governance fact snodo produces and appeared
  in no audit event at all; it existed only in the local structured payload. A
  reader with the chain alone, which is the only tamper-evident artefact snodo
  produces, could not tell an escalation from a blocker from an engine fault.
  `halt_type`, `final_decision` and `raw_halt_type` are now on the event, so
  the specific type survives alongside the coarse one. (Fixes #204, Refs #203)

### Fixed

- Reading a project's identity no longer writes a file. `get_project_id`
  persisted as a side effect of resolving, and #202 made that path reachable
  from the default audit log, so any command touching it could create
  `.snodo/project.json` in whatever directory resolved as the project root —
  including one the operator never initialised. A read-shaped call had acquired
  a write side effect and become reachable from everywhere. Resolution is now
  read-only; persisting an identity is an explicit step taken by `snodo init`
  and by session creation, the two places that establish one.
  (Fixes #207, Refs #205, Refs #202)

- A coder that runs to completion and writes nothing is no longer reported as
  an engine failure. The executor raised a bare `ExecutionError`, which had no
  canonical mapping and so resolved to `internal_error`, telling the operator
  that a validator or the engine had crashed and to inspect the logs — while
  the coder had in fact exited zero and simply declined to write. It is now a
  named `no_file_operations` blocker whose hint points at the task spec and the
  coder configuration rather than at produced code that does not exist. This is
  the sibling of #195, which gave the same treatment to a coder backend that
  fails to run. (Fixes #203, Refs #195)

- Attribution can no longer crash a completed run. The check that an in-place
  adapter declares `coder_name` raised after the coder's work had been
  committed, so a subclass that omitted it would turn finished work into a
  crash at the accounting step — the one thing attribution must never do.
  Enforcement moved to the adapter conformance test, where the omission is
  caught at test time. (Refs #206)

- The telemetry drop file is bounded. `.snodo/telemetry_drops.json` grew
  without limit and was rewritten whole on every append, so the scenario where
  it mattered — a run dropping every record — was exactly the one where the
  rewrite became quadratic. Telemetry must never harm the run it observes.
  (Refs #206)

- An explicitly supplied `mock_files` now wins over the language fixture. The
  mock coder substituted its built-in JavaScript fixture even when the caller
  had passed files, which is inference overriding a stated instruction — the
  same defect the containment fix removed from the subprocess adapter. (Refs
  #206)

- The merge gate's commit comparison is one-directional. It accepted a prefix
  match in either direction, so a stored abbreviation could in principle match
  a commit that was not the merge target. A stored value must now be the full
  SHA or a genuine abbreviation of the target. (Refs #206)

### Changed

- `docs/specs/cloud-sync.md` is now the emit contract rather than the
  implementation ticket it began as. It describes the payload as it actually
  is, project identity semantics including why local scope is deliberately
  unreconcilable, all transmitted event types and their data keys, and the
  never-transmitted list as stated non-goals. The contract is 0.x and tracks
  `main`. `SECURITY.md` now names cloud sync egress as a security surface.

---

## [0.7.2] — 2026-09-02

### Added

- In-place coder runs now leave an attribution record. agy, the opencode CLI
  and the opencode container make no litellm calls, so the usage callback never
  fired for them and a completed run was indistinguishable in `state.json` from
  one that genuinely cost nothing. Runs now record wall-clock duration and the
  model the tool was asked to use, with `cost` and token counts explicitly
  `None` rather than zero, and a `measured` list declaring which fields were
  directly observed. (Fixes #69)

- The reduced local test gate now announces itself. `uv run pytest tests/` runs
  with `addopts = "-m 'not e2e'"`, so it skips the e2e suite (~124 tests) while
  CI clears the filter and adds a coverage floor and the patch-coverage check.
  The two commands looked alike and were not the same gate: a clean local run was
  repeatedly followed by a CI failure the local command could not see. A passing
  reduced run now prints a notice on the same screen naming what was skipped and
  the command that matches the gate CI applies. A guard test
  (`tests/test_verification_gate.py`) fails if pyproject, `ci.yml` and
  `CONTRIBUTING.md` drift apart again without that difference being stated, and
  the fast loop is unchanged (e2e still deselected by default). (Refs #87)

### Fixed

- An in-place coder can no longer infer its own containment boundary.
  `SubprocessCoderAdapter` fell back to `Path.cwd()` when no workspace was
  passed, or when the workspace MCP was not the expected type — silently, twice
  — and that value became the directory granted to the external CLI and the
  subprocess cwd. A run whose cwd was the main checkout was therefore contained
  to the main checkout, which is how work landed outside its task worktree. The
  boundary must now be stated by the caller; an unstated one is refused at
  construction. (Fixes #201)

- `snodo task list` no longer reports a status or a timestamp it could not
  establish. Failed git inspection was resolved by reporting tasks as
  completed, an unreadable audit log left merged tasks reported under some
  other status, and an unknown timestamp was replaced with `now()` — which made
  such tasks permanently unprunable and displayed a moment that never happened.
  Unestablished statuses now read as unknown, and `snodo task prune` skips
  untimestamped tasks with a count instead of acting on an invented date.
  (Fixes #179)

- The mock coder no longer produces a passing gate for projects it knows
  nothing about. It emitted Python fixtures unconditionally, so in a JavaScript
  project the test runner found nothing to fail and the quality gate returned a
  pass. The mock is reachable from the CLI, so this was a green gate an
  operator could see, not only a test-suite wart. Fixtures now follow the
  project's declared language; the Python case is unchanged. (Fixes #187)

- The merge gate now checks the provenance of the verification it relies on.
  It accepted any `verification_executed` event in the project's audit log with
  a passing outcome, ignoring the `task_ref` and `commit` the event already
  records — so a pass belonging to a different task at a different commit
  satisfied the gate. Evidence must now match the task and the commit being
  merged, and both the accepting and refusing paths name the evidence they
  relied on. (Fixes #76)

- Discarded turn telemetry is no longer invisible. Records were dropped
  silently in five places, so an empty telemetry section could not be told
  apart from a run that produced no turns. Drops are now persisted where a
  project root resolves and surfaced with their reason in `snodo meta`; when
  the project root itself is what could not be resolved there is nowhere
  durable to write, and the drop stays in process. (Fixes #180)

- `snodo task review --pending` now shows the operator's original request.
  Excerpts were read only from the active session's halt payloads, so units
  merged in any earlier session — the ordinary case for this command — showed
  blank, and units that had gone through recovery or retry showed engine
  scaffolding instead of the request. The spec is now recovered from durable
  records, retry and recovery wrappers are unwrapped at display time, and an
  unrecoverable description is distinguished from an empty one. The
  `task_merged` event now records the authoritative spec raw, since the audit
  log is append-only attestation and must not carry a parsed value. (Fixes #149)

- The test suite no longer leaks environment variables between tests. A
  provider-credential test wrote `OPENAI_API_KEY` into the real environment and
  later tests inherited it; a guard now reverts and fails on any environment
  change a test leaves behind, and caught five further leaks on introduction.

- The default audit log now resolves against the project root, not the process
  cwd. A task running from its worktree (background jobs spawn the CLI with
  `cwd` set to the task worktree) used to point the default
  `.snodo/audit.log` at a throwaway log inside the worktree — the branch under
  judgement — so the audit trail, and the merge gate reading its
  `verification_executed` history in `_merge_on_success`, witnessed a
  worktree-local chain instead of the project's. The default now resolves via
  `resolve_project_root()` (honoring `SNODO_PROJECT_ROOT` set by the job
  wrapper, and otherwise walking up from the cwd), falling back to the
  historical cwd-relative path only when no project root is resolvable. The
  audit log remains a property of the project, never redirected by
  `SNODO_HOME`. (Fixes #73)

- A coder backend that fails to run is no longer reported as an internal
  engine error. The executor used to wrap every exception from
  `coder.implement()` into `ExecutionError`, and the execute node reported
  `ExecutionError` as `internal_error` — so an operator's configuration
  mistake (a model string the coder's CLI rejects, a binary missing from
  PATH, an LLM call that errored) halted with `halt_type: internal_error`
  and a hint telling them to inspect engine logs, while the reason field
  already held the exact answer. `AdapterError` (the base of `LLMCallError`
  and `ParseError`) now propagates unchanged and halts under the raw
  `execution_error` (canonical `blocker`) with a config fix target — "Fix
  the coder configuration — the model string, the coder backend (--coder),
  or the tool's install — then re-run" — and recovery does not spawn against
  it. Engine-level `ExecutionError` (no file operations, an unexpected
  adapter bug) remains `internal_error`. (Fixes #195)

- The ADR 040 search tools no longer consume runs. The `search_string` /
  `search_symbol` schemas and prompt text now say explicitly that the first
  parameter is the text or identifier to find and that folders belong in
  `directory`; a directory passed in the search-term slot returns a one-turn
  correction instead of a full-tree scan. `ReadMemoryTracker` canonicalizes
  dedup keys the way the resolver treats paths — default, `null` and absent
  optional arguments are one call, and `./x`, `x//y`, `src\x` and absolute
  paths under the project root all address one file — so a repeated search
  comes back as a turn pointer instead of repeating, and two spellings of a
  path deduplicate to a single read. (Fixes #184)

- A coder that exhausts its tool-loop turn budget without submitting is now a
  nameable halt, not an internal error. The coder loop used to raise a generic
  `ParseError`, which the executor wrapped into `ExecutionError` and the
  execute node reported as `internal_error` — so a bounded, anticipated
  outcome looked like a crash, and the recovery ladder kept spawning attempts
  against a condition retrying could never change (three identical ~20-minute
  exhaustions). It now surfaces as a distinct `TurnBudgetExhausted` exception
  mapped to the raw halt `turn_budget_exhausted` (canonical `blocker`, fix
  targets spec/policy), and recovery does not spawn for it. (Fixes #191)

- Task retry (`snodo run --retry <task_id>`) now constructs a clean prompt per
  attempt instead of recursively wrapping previous prompt blobs. `original_spec`
  is preserved across retries in session `task_failure` and `root_spec` is
  threaded to the Task so task branch slugs, path-existence pre-flight checks,
  and halt payloads use the authoritative spec rather than nested prompt text.
  The prompt construction now names the actual failure phase (`pre-validation`,
  `at execute`, `post-validation`), omits the `Files changed` line when empty,
  and only includes the latest attempt's failure context. (Fixes #197)
- `snodo plan run` now exposes the full shared execution surface of `snodo run`
  — `--coder`, `--mock`, `--mode`, `--verbose`, `--no-isolation` and
  `--retain-worktree` were declared only on `snodo run`, so a plan — the
  surface an orchestrator drives — could not select a coder or mock a run.
  The two commands now bind a single shared option declaration in
  `snodo/cli/commands/run_cmd.py`, and `tests/cli/test_surface_parity.py` pins
  the two command surfaces against each other so the next option added to one
  fails loudly if it is missing from the other. (Fixes #186)

---

## [0.7.1] — 2026-08-31

A hardening release: no behaviour change to the protocol engine. The supply
chain, the release pipeline and the docs build all gained gates, and the
documentation caught up with the coder work shipped in 0.7.0.

### Added

- Security gates in CI: CodeQL, OpenSSF Scorecard, `pip-audit` and `zizmor`,
  plus PEP 740 build attestations on published artifacts. (Fixes #162)
- Coverage is measured and uploaded to Codecov, with the gate raised to 75%
  (measured: 78% over 2,977 tests). (Fixes #160)
- A `docs` extra, a strict `mkdocs build`, `make docs` / `make docs-serve` /
  `make deploy-docs` targets, and a `coders.md` page. (Fixes #158)
- Execution-model diagrams for the three `snodo run` paths (worktree, docker,
  in-place) and for `snodo plan` waves, embedded in `coders.md`,
  `authoring-a-plan.md` and the README.
- The recovery prompt now carries the prior attempt's read-set (paths only), so
  a retry starts warm instead of re-discovering the same files.
- Each tag now creates a GitHub Release with the changelog body and the dist
  artifacts attached.
- A 7-day Dependabot cooldown, so a freshly published release is not adopted
  the hour it lands.

### Fixed

- Validator severity is now reported to the progress callback *after* capping,
  so the terminal and the audit record agree on what a validator returned.
- README and `coders.md` describe the actual provider surface: Cloudflare
  Workers AI, Ollama Cloud, local Ollama and llama.cpp, and any
  OpenAI-compatible endpoint. ADR 035's capability list is scoped to the
  litellm coder, which is the only one that has those capabilities.
- Locked dependencies upgraded to clear 52 advisories across 11 packages;
  `datasets` bumped to 5.0.1 (PYSEC-2026-3716); `mcp` pinned below 2.x, where
  `FastMCP` was renamed to `MCPServer`.

---

## [0.7.0] — 2026-08-31

The release that makes the coder swappable. `litellm`, `opencode`,
`opencode-cli`, `agy` and `mock` are now interchangeable on the same project
under the same gates: validators and the classifier no longer borrow their LLM
client from the coder, so a subprocess coder that has none is judged exactly as
the in-process one is. `--coder` selects the backend independently of the model,
and the run banner, halt payload and audit trail all name which coder actually
did the work.


### Added

- Accumulated `submit_files` file operations across turns in `LiteLLMAdapter`, delivering the complete accumulated set atomically when complete or when turn budget finishes instead of terminating on the first `submit_files` call. Added `read_files` batch read tool and explicitly configured `parallel_tool_calls=True` in completion requests. (Fixes #152).
- Documented interchangeable coder backends (`litellm`, `opencode`, `opencode-cli`, `agy`, `mock`), `--coder` selection precedence, model role separation (judging vs execution), in-place coder commit ownership and `.snodo/` boundary protection, installation/authentication requirements, and `SubprocessCoderAdapter` extension guidelines across `README.md`, `docs/protocol.md`, and `docs/architecture.md`. (Fixes #147).

### Fixed

- A session id cited by the project audit log is no longer silently invisible
  to session readers. The audit log is a property of the PROJECT (never
  redirected by `SNODO_HOME`), while session files are stored under the snodo
  home; a session created under one home is therefore audited into the shared
  `audit.log` but absent from every other home's session store — `snodo session
  show/list`, `snodo task show`, and `snodo cloud sync --session` all reported
  a bare "Session not found", and the active-session pointer silently
  auto-adopted a *different* session. Those readers now detect the divergence:
  `session show`/`cloud sync --session` name the audited-but-missing id and the
  store it is absent from, `session list` lists audited ids with no file, and
  `get_active_session` warns (and audits `session_pointer_audited_but_missing`)
  before falling back to auto-adoption, instead of attributing tasks to a
  session that never ran them.

- A run now records which coder produced the work. `snodo run` previously
  printed "Coder: real LLM" for every non-mock coder, and neither the halt
  payload nor the audit trail carried a coder field — a run under litellm,
  opencode and agy were indistinguishable afterwards, in the output and in
  the record. The resolved coder is now named in the run banner, in the halt
  payload (`coder` alongside `model`), and in the `dispatch` audit event,
  using the registry name the operator would pass to `--coder` (e.g.
  `litellm`, `opencode-cli`, `agy`), not a class name. A completed task also
  clears any pending decision left by an earlier failed attempt — a standing
  proposal to proceed past a blocker that no longer exists must not survive a
  successful run — while a task that halts still records one. (Fixes #148).

- Cloud sync is no longer best-effort, silent, and racing process exit. The
  automatic sync started in a daemon thread and was killed the instant the
  command returned — the POST has a 30s timeout and up to 5 backoff retries,
  so it routinely needed longer than the process lived, and every failure
  ended at a `_logger.warning` that had no handler without `--verbose`. On a
  real project it synced nothing for two months before the operator noticed.
  `sync_if_enabled` now starts the sync in a daemon thread and registers it
  for a bounded wait at process exit (`flush_pending_syncs`, an atexit flush):
  a sync that completes within the budget is delivered and the cursor
  advances; one that exceeds the budget is abandoned (the cursor stays put,
  so the events re-send next time) and the operator is told on stderr in one
  line; a failed sync is also reported on stderr without `--verbose`. The
  whole flush fits inside one bounded budget however many syncs are pending —
  a ten-task plan waits at most one budget, not ten — and the reported pending
  count is the unsynced backlog (events past the cursor), not the size of the
  whole log. The wait is always bounded: threads stay daemon, so a slow or
  unreachable cloud never hangs the CLI. `snodo cloud status` now answers "is
  my audit trail actually reaching the cloud?": per session it shows how many
  events are pending, when the last attempt was, and what went wrong if it
  failed (a confirmed success clears the pending count and the error). The
  cursor still advances only on a confirmed success, the audit log on disk
  stays the source of truth, and `AuditLog.append_event` is untouched.
  (Fixes #142).

- Every snodo command no longer prints LiteLLM `register_model` warnings at
  import. `snodo --version` on a clean install previously emitted five
  `LiteLLM:WARNING: register_model: model=... not in built-in cost map`
  lines — the first thing a new user saw, publishing snodo's model catalog
  into the terminal. The warnings fired because the Cloudflare model entries
  registered in `snodo/coders/litellm.py` lack cache cost fields, and the
  module set the LiteLLM logger to WARNING permanently. The logger is now
  suppressed to CRITICAL for the duration of the `register_model` call only
  and restored to its previous level afterwards, so a real cost or routing
  warning during a run still surfaces. (Fixes #135).

### Added

- Added CLI test coverage in `tests/cli/test_install_cmd.py` and `tests/cli/test_models_cmd.py` covering happy paths, empty configurations, unconfigured provider errors, `--flush` cache handling, generic OpenAI-compatible model discovery against stubbed endpoints, orphan MCP detection/removal, and purge state cleanup. (Fixes #134).
- `snodo plan` can now execute and author plans, not just create and inspect
  them. Four subcommands were added to the plan Typer app: `snodo plan run
  <name> [--wave N] [--interactive] [--protocol PATH] [--model M]` builds the
  same SimpleNamespace `snodo run --plan` builds and delegates to
  `plan_run._run_plan` (imported inside the function, so `snodo run --plan`
  is unchanged); `snodo plan add-task <plan> <task_id> --spec-file PATH
  [--parent REF] [--replace]` reads the spec file and calls
  `planner.generate_spec`, rejecting task ids that are not `<wave>.<seq>_<name>`
  (e.g. `1.1_models`) and treating a missing spec file as an error, not an
  empty spec; `snodo plan add-wave <plan> <id> [--depends-on 1,2]` adds a wave
  to plan.yml, refusing non-integer ids, duplicate ids, and dependencies on
  waves that do not exist; and `snodo plan delete <name> [--force]` removes a
  plan directory, refusing without `--force` when any task is completed or
  in_progress and naming them. After add-task and add-wave the plan is
  re-verified with `verify_plan_dir`; if it no longer verifies, the errors are
  reported and the plan is marked invalid rather than silently broken.
  (Fixes #130).

### Changed

- Updated `PlannerMCP.decompose` to return an honest empty plan scaffold with a configurable `waves` parameter (defaulting to 1 empty wave so a freshly created plan passes `verify_plan` validation), and added comprehensive test coverage in `tests/mcp/test_planner.py` for spec generation, depth resolution, ancestor cycle checks, `update_status`, `recompute_depths`, and malformed plan directory handling. (Fixes #129).
- The flake8-bandit security subset is now triaged at each site. Every
  `S602`/`S603`/`S607`/`S105`/`S311`/`S314` finding in `packages/` and
  `snodo/` carries a scoped `# noqa` with its own rule code and reason. Both
  `S602` (`shell=True`) sites — `validators/quality.py` and
  `infrastructure/environment.py` — are operator/protocol-authored shell
  strings (test command and prepare command) sourced from the trusted
  repository per ADR 014; compound commands require a shell, so they stay
  `shell=True` with the trust rationale documented. The `S603`/`S607` sites
  are all argv lists (no shell) where git/docker/opencode are resolved from
  PATH by design; inputs from task specs, plans, or config are passed as
  single argv elements, never shell-interpreted. The two `S314` sites parse
  snodo's own `coverage.xml` output from the CI gate. The `S105` finding is
  the `Severity.PASS` enum name (not a credential) and the `S311` finding is
  a non-secret tunnel `short_id` (not a credential). (Fixes #123).

### Fixed

- Decoupled validator and classifier completion function resolution from the coder object in `packages/snodo-engine/src/snodo/engine/loop.py`. Coders without a `_completion_fn` (opencode adapters, binary/shell coders, or external agents) now have their validator runner and classifier completion functions constructed from configuration using `litellm.completion` bound to `validator_model` and `classifier_model`. Preserved exact behavior on the `litellm` path, maintained per-validator `model:` override precedence, preserved mock completion resolution under `--mock`, and ensured missing/unusable validator clients fail closed with `error=True`. Added comprehensive tests in `tests/engine/test_coder_completion_seam.py`. (Fixes #143).

- Fixed cloud sync treating un-retryable 4xx client errors identically to retryable failures by classifying sync POST outcomes into `delivered` (2xx), `retryable` (429, 5xx, network errors), and `refused` (4xx client errors except 429). Refused sessions leave the cursor, record refusal reason and sequence range in `cloud_sync.json`, stop automatic retries on subsequent sync runs, surface as `BLOCKED (refused: ...)` in `snodo cloud status`, and provide operator `--force` / `--retry` escape hatch on `snodo cloud sync` to re-attempt delivery, clearing blocked status on 200 OK. (Fixes #141).

- Fixed `snodo plan run <name>` crashing with `AttributeError` by defining `RunArgs` frozen dataclass in `snodo/cli/commands/run_cmd.py` containing all execution fields defined by `run`. Updated `plan_cmd.plan_run` and `job_cmd._retry_job` to construct `RunArgs`, guarded `args.verbose` access inside `_build_graph` exception handlers, allowed `AttributeError` and `TypeError` to propagate from `_build_graph` instead of masking programming bugs as config errors, and updated `tests/cli/test_plan_cli.py` with derived parameter inspection and end-to-end `--mock` plan execution tests. (Fixes #140).

- Fixed `_select_template` in `snodo/cli/commands/init_cmd.py` silently creating a `team` protocol on prompt abort (`KeyboardInterrupt`) or non-interactive stdin (`EOFError`). KeyboardInterrupt now aborts with a cancellation message and exits non-zero; EOF without `--template` reports that `--template` is required in a non-interactive context and lists available templates. Updated Next steps output to include provider credential setup (`OPENAI_API_KEY` or `snodo config set`), and added comprehensive behavioral test suite in `tests/cli/test_init_cmd.py`. (Fixes #133).
- `snodo run` now checks for provider credentials before starting any work. Previously a
  fresh install loaded the protocol, opened a session, created a worktree, built and
  compiled the MCP graph, and only then failed with `litellm.AuthenticationError: Missing
  Anthropic API Key ...`. A preflight in `run_command` resolves the model's provider (via
  the same `ConfigManager` helpers that load the key, so it cannot disagree with them) and
  fails immediately with one line naming the provider, the expected environment variable or
  config key, and the command to set it — exit 1, nothing created. The check is skipped when
  the coder is mocked (`--mock`) or when no LLM will be called. (Fixes #137).
- `ProtocolAdherenceValidator` no longer reports fail-open `warn` when it could not
  execute. Its catch-all previously returned `severity="warn"` when the LLM call
  raised — implying the gate ran and was mildly unhappy — which lets work pass on a
  validator outage under non-unanimous policies. An exception from the call now
  returns `severity="blocker"` with `error=True` and a justification naming it an
  operational error (the shape `llm_validator` and this validator's provider-rejection
  branch already used). Genuine parse-level warn verdicts are unchanged: they still
  report `warn` with `error` unset. (Fixes #136).

- A blocked task in a plan no longer restarts from scratch on re-run. `_execute_wave_task`
  previously re-executed a task the status file marked "blocked" as a fresh dispatch,
  discarding the failure context `_auto_write_failure_context` persists. A blocked task with
  failure context now resumes through the retry path — reusing `_retry_task`'s context
  resolution (prefers `decisions["task_failure"][id]`, falls back to the halt record) and
  its `max_retries` enforcement. A task at `max_retries` is not re-executed and prints the
  abandon/override guidance; a blocked task with no failure context runs fresh and the line
  says so, so the operator can tell the two apart. Completed tasks are still skipped.
  (Fixes #131).

- Enforced ruff rule `B904` (`raise-without-from-inside-except`) across `packages/` and `snodo/`. Updated 61 exception re-raise sites to explicitly attach causal exception chains (`from e` or `from None`), ensuring underlying error details (e.g. git command failures, GitHub API exceptions, container errors, and token store errors) are preserved for structured audit logs and halt payloads. (Fixes #122).

- Fixed 46 silent try-except-pass blocks across `packages/` (Ruff rule S110) so exceptions in audit log, session persistence, process execution, and resource cleanup are properly logged at warning or debug level instead of being silently swallowed. (Fixes #124).
- A corrupt job `state.json` is no longer silently destroyed. `_merge_into_job_state`
  previously treated a parse failure as "no state" and rewrote the file containing only the
  incoming updates, discarding the halt payload, per-turn tool telemetry (#105), and every
  other recorded field. A parse failure (unparsable JSON, or JSON that is not an object) now
  preserves the original file under `state.json.corrupt-<timestamp>`, records a
  `job_state_corrupt` audit event, and raises `JobStateError` — the default is refusal, and a
  caller that must tolerate corruption opts in by catching it. (Fixes #127).

- `snodo task show <task_id>` now prints the task spec — the one field an
  operator needs to act on a failure. Previously it showed the halt type, the
  hint and every validator justification but not the spec, forcing an operator
  to dump the whole session JSON to recover it. The spec is read from
  `decisions["task_failure"][task_id]["spec"]` when present, falling back to
  `decisions["halt"][task_id]["task_spec"]`, and is included in the `--json`
  output under a `"spec"` key verbatim and untruncated. In the human-readable
  form it is shown last under a clear "Task spec:" heading, truncated at 400
  characters with a note pointing at `--json` for the full text. (Fixes #117).
- `snodo run --retry <task_id>` now falls back to the persisted halt record when no `task_failure` entry exists for the task — halts recorded before commit eab9696 (or on any path that wrote a halt but no failure context) no longer dead-end with `No failure context for <task_id>. Cannot retry.` `_retry_task` synthesises the failure context from `decisions["halt"][task_id]` (spec from `task_spec`, failed validators from `validator_results` or `reason`, attempt 1, branch via `_task_branch_name`, empty `files_changed`), but only when the halt record's `task_id` matches and it is a blocked halt; `task_failure` remains the preferred source when present. (Fixes #121).

- Fixed execution-phase halts missing failure context for `snodo run --retry`. When execution fails (e.g. coder error, missing provider credentials, token store error, or `head_not_moved`), `_execute_node` now calls `_auto_write_failure_context` so structured failure context is persisted to the session checkpoint decision store, allowing `--retry` follow-ups to succeed instead of failing with `No failure context for <task_id>. Cannot retry.` (Fixes #116).

- Concurrent snodo processes can no longer corrupt the audit log's hash chain.
  `AuditLog.append_event` previously derived the next sequence and previous
  hash from process-local memory, so two processes both wrote "their" next
  sequence — observed twice in one day as "sequence discontinuity on line 1325
  (expected 1324, got 1323)" and "line 39 (expected 38, got 28)". Appends now
  take an exclusive file lock (`fcntl.flock`), re-read the file's last line
  under the lock to derive the true sequence and previous hash, write, and
  release; the in-memory `events` list is a cache only, never the source of
  the next sequence. If the lock cannot be acquired within 10s, `append_event`
  fails loudly with `AuditError` — it never silently proceeds, because the
  audit log is the attestation. (Fixes #114).

- Added typed Pydantic models (`Plan`, `PlanWave`, `PlanTask`) and well-formedness verification (`verify_plan`, `PlanWellFormednessError`) for plans. `PlannerMCP.get_plan()` and `validate_plan()` now verify plan integrity on every load, catching unknown parent task references, parent reference cycles, wave dependency cycles, wave-number gaps, status entries with missing tasks, and tasks with missing spec files. Malformed plans are refused at load time with `PlanWellFormednessError`. (Fixes #113).
- Fixed missing verdict message in `snodo task review` CLI command. When no verdict is provided, the command now cleanly reports that a verdict is required rather than printing `Invalid verdict 'None'`. Added comprehensive test suite for `snodo/cli/commands/task_cmd.py` covering `task_report`, `task_review`, `task_list`, `task_show`, `task_abandon`, and `task_prune`, raising test coverage from 28% to 90%. (Fixes #112).
- Fixed post-execute quality blockers being downgraded to warn during recovery attempts. `PolicyEvaluator.evaluate()` now requires `phase` explicitly, `_post_validate_node` passes `phase="post_execute"` and `is_recovery`, the recovery pre-execute escape branch no longer uses `"_fix_" in task_ref` substring matching, and validator contradiction detection now marks acceptance claims as `blocker` rather than `warn`. (Fixes #106).
- `InPlaceCoderAdapter._commit_changes` failure path diagnosis and `head_not_moved` halt payload cause reporting. `_commit_changes` in `snodo.coders.base` now sets a structured `last_commit_reason` across all failure paths (`cannot_open_repo`, `git_add_failed`, `nothing_staged`, `git_commit_failed`) and logs warnings when `git add` stages nothing. The engine records `last_commit_reason` during execution and surfaces `commit_reason` directly in the `head_not_moved` halt payload, allowing operators to distinguish coder no-op runs from git command or workspace errors. Additionally, `_diff_to_artifact` in `opencode_cli_adapter.py` now labels unreadable files with `<unreadable: ...>` instead of swallowing read errors into empty content strings. (Fixes #108).

### Added

- Added `plan` mode to the `greenfield` protocol template (`greenfield.yml`). The `plan` mode grants authoring capabilities (`plan` group: `decompose`, `generate_spec`, `validate_plan`, plus `read` group: `read_file`, `list_files`), `meta-spec` validator, and handoff transition to `decide`. Added `"read"` capability group mapping to `MODE_TOOL_MAP` in `tools.py` and added test suite `tests/mcp/test_plan_mode.py` verifying tool resolution and closed-by-default capability refusal for modes lacking `plan`. (Fixes #128).

- `try`-`except`-`pass` blocks in the `snodo/` root package are eliminated
  (S110). Every site was a place where a failure was swallowed in total
  silence. Each was fixed by logging the exception (`logger.debug`/`warning`
  with the exception), handling it, or deliberately suppressing a named
  exception type with a comment. One genuine swallowed defect was found and
  fixed: `install_cmd._audit_global` silently dropped failed audit appends —
  an audit write that fails is part of the attestation and must not be silent;
  it now logs a warning. The dashboard cockpit's cursor-positioning sites
  suppress `RowDoesNotExist` (a filtered-out row simply leaves the cursor
  put). (Fixes #125).

- `snodo task review --pending` lists every merged unit with no review record,
  newest first, with unit id, task id, branch, merge timestamp and a one-line
  spec excerpt. `--json` emits the machine-readable form
  (`snodo.task_review_pending.v1`). It is read-only: it reads the audit log
  (`task_merged` / `human_review_recorded` events, keyed on the merge commit
  SHA) and the session halt payloads for the spec excerpt, and never creates,
  mutates or clears any review record. The empty case prints a clear message
  and exits 0. (Fixes #120).
- Added behavioral test suites for CLI commands `snodo meta` (`tests/cli/test_meta_cmd.py`), `snodo status` (`tests/cli/test_status_cmd.py`), and `sandbox_run` (`tests/cli/test_sandbox_run.py`). Covers happy paths with populated sessions, empty/no-session paths, `--json` machine-readable output format, non-zero failure paths, and per-turn tool-loop telemetry summary formatting. (Fixes #118).

- Added `snodo plan validate <name>` CLI command and plan pre-verification in `snodo plan run`. Added `verify_plan_dir` in `snodo.compiler.verifier` to load and verify hand-authored or decomposed plan directories. `snodo plan validate <name>` runs well-formedness verification (detecting wave-number gaps, wave dependency cycles, parent reference cycles, unknown references, missing spec files, and orphan status entries) with optional `--json` machine-readable output. `snodo plan run` pre-verifies the entire plan directory before wave 1 dispatches any tasks, aborting with exit code 1 if errors are found, while printing warnings without aborting. (Fixes #119).
- Custom OpenAI-compatible provider support and decoupled litellm routing. Added `litellm_provider` and `extra_headers` fields to `ProviderConfig`. `ConfigManager` now decouples snodo config key resolution (`_provider_for_model`) from litellm model formatting (`resolve_litellm_model`) and header resolution (`resolve_extra_headers`), eliminating hardcoded provider special cases. `model_discovery.py` now includes a generic `_discover_openai_compatible` fallback fetcher for custom provider endpoints (such as Ollama Cloud at `https://ollama.com/v1`). Documented custom OpenAI-compatible provider configuration with an Ollama Cloud worked example in `README.md`. (Fixes #115).
- Per-turn tool-loop telemetry is now persisted to each job's `state.json`
  under the `tool_telemetry` key instead of being printed and discarded. Both
  the coder loop (`coders/litellm.py`) and the validator loop
  (`validators/llm_validator.py`) emit one structured record per turn:
  `task_ref`, `depth`, `attempt`, `role` (coder|validator), `validator_id`,
  `turn_index`, `tool`, `target_path` (workspace-relative and canonical),
  `read_hit` (sourced from `ReadMemoryTracker.check_read`, not recomputed),
  `tokens_in`, `tokens_out`, `elapsed_ms`, and — on the terminal coder turn —
  `submit_bytes`. This is operational telemetry, NOT part of the hash-chained
  audit log (ADR 034): ~50 records per task do not belong in the attestation
  chain. `snodo meta` now reports, per job: orientation ratio (turns before
  first write / total), path miss rate, re-read rate by depth, and submit-size
  distribution. Terminal output is unchanged — this adds a sink, it does not
  replace one. The opencode adapters own their own agent loop and cannot emit
  these records; their runs have no per-turn telemetry — an acknowledged
  consequence of ADR 034's experimental status. (Fixes #105).

- The adapter conformance gate now matches the post-execute review contract.
  `tests/coders/test_adapter_conformance.py` previously asserted the review
  diff against `HEAD~1..HEAD` and taught that stale contract in its docstring;
  #103 moved post-execute validators onto an explicit `base_ref..HEAD`
  (the execute-node HEAD anchor captured before the coder runs). The gate now
  captures the anchor before the driver runs and asserts `base_ref..HEAD`,
  matching what `llm_validator`/`acceptance` read. A new conformance case,
  parameterised over the same registry, drives the engine graph with the
  commit disabled and asserts the run is refused with `halt_type
  == "head_not_moved"` — the no-commit case that occurred in production and
  that #103 exists to catch. The validator fallback is now tested too: when
  `base_ref` is absent the tool loop falls back to `HEAD~1..HEAD` AND labels
  it as a fallback in the judge's prompt ("no execute-node HEAD anchor was
  available"), while a present `base_ref` diffs `base_ref..HEAD` with no
  fallback label. (Fixes #109).

- `snodo task review` report now counts every merged unit, not just the last
  verdict per worktree branch. The report previously keyed review verdicts on
  `task_ref`, which the merge path records as the worktree branch name — N
  merges of one branch collapsed to one row and only the last verdict
  survived, bounding the report's denominator at the number of worktrees
  (five) however long tagging ran. Each `task_merged` / `human_review_recorded`
  event now carries a `merge_sha` (the merge commit) as the unit identity,
  with the branch name kept as a separate human-readable field; the report
  keys on that identity, so N merges of one worktree are N rows. The report
  also reads `task_complete` (the engine's completed-task event) for the
  completed-task denominator, and the two numbers are labelled distinctly in
  both text and `--json` output (`completed_tasks` vs `merged_units`) — they
  answer different questions and conflating them was the bug. The audit log is
  hash-chained, so this is additive only: the 7 pre-existing events recorded
  without a `merge_sha` are treated as lost (they fall back to `task_ref` and
  still collapse), and the report's merged-unit count starts fresh from the
  first SHA-stamped merge. (Fixes #101).
- `GraphBuilder` method shadowing over mixin classes removed and canary gate added. Moved live node method implementations (`_governance_node`, `_validate_node`, `_execute_node`, `_post_validate_node`, `_route_after_validation`, `_route_after_post_validation`) into their respective mixins (`GovernanceNodeMixin`, `ValidationNodeMixin`) and deleted redundant shadowing copies in `GraphBuilder` (`snodo/engine/loop.py`). Added MRO method shadowing gate test (`tests/engine/test_graph_builder_mro_gate.py`) to prevent `GraphBuilder` from re-defining methods already declared on its mixins. (Fixes #100).

- The worktree path checker no longer flags slash-containing prose as a
  missing path. `_spec_referenced_paths` previously treated any token
  containing a `/` as a cited path, so a spec sentence like
  `rel=noindex/no-referrer` was flagged as a missing file alongside genuine
  citations — a guard that cries wolf gets ignored, and this one guards
  against a failure that already cost a whole task once. A token is now
  path-like only when it ends in a file extension (`a/b/c.ext`), ends in a
  trailing slash (`a/b/c/`), or has at least three slash-separated segments
  (`a/b/c`). The deliberate trade-off: a two-segment extensionless path
  written in prose (`src/parser`) is now missed, and a path named without a
  path-like token was already missed. (Fixes #99).
- Runbooks in `docs/runbooks/` updated to capture today's closed defect patterns and verification lessons. Documents gate canary principles (gate canary necessity proved by 4 canaries written this week; Fixes #58, #74, #81 / ADR 037), pre-execute validator tree-state recovery deadlocks (Fixes #90), spec untracked path unresolvability and coder authorship transfer (Fixes #93), test suite silent under-collection (Fixes #98), and audit hash chain corruption masking (Fixes #96).
- ADR 038 added to formally define the Orchestrator role operating outside the inner execution loop (`engine/loop.py`), its permissions and boundaries, and its audit trail contract. Defines what the orchestrator MAY do (framing task specifications, defining criteria, issuing signed `snodo authorize` adjudications) and MAY NOT do (mutating loop state or bypassing validation gates). Establishes that task specifications and orchestrator decisions MUST be immutably recorded in `.snodo/audit.log` as first-class audit events (`task_spec_authored`, `task_decomposed`, `orchestrator_decision_issued`) to eliminate post-mortem analysis blind spots.

- Release publishing is no longer ungated. The release workflow now validates the pushed tag against `pyproject.toml` and `CHANGELOG.md`, then runs `pytest`, `ruff`, and `lint-imports` before any build or publish step. The tag/version/CHANGELOG comparison lives in `scripts/check_release_version.py` so the refusal path is unit-tested without pushing a tag. Historical discrepancy noted: tag `v0.6.1` points at `d5b2138 release: v0.6.0`, `pyproject.toml` declared `0.6.1`, and `CHANGELOG.md` has no `[0.6.1]` section; it is intentionally not retagged or rewritten. (Fixes #102).

- Silent under-collection in pytest test suite execution prevented. `pytest_configure` in `tests/conftest.py` now asserts that pytest's resolved `rootdir` matches the workspace git root, preventing package-local or subdirectory rootdir resolutions from running partial test suites. Additionally, `pytest_collection_modifyitems` enforces a minimum collection count threshold (`>= 2000` tests) when targeting full suite paths (`tests/` or default), causing under-collection to fail loud with a `pytest.UsageError` instead of reporting false passes. (Fixes #98).

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

### Removed

- `snodo merge`, the `ci-gate` infrastructure, `ci-wait`, and
  `scripts/merge-agents.sh` are removed. This was the author's multi-worktree
  development workflow, not a product feature, and it is not coming back —
  branch CI status belongs on `CodeHostProvider` if a product feature ever
  needs it. The product merge path is untouched: `worktree.merge_task_branch`,
  `resolve_base_branch`, `merge_head_sha`, and `run_cmd._merge_on_success`
  remain, and `run_cmd` is their caller. (Fixes #104).

### Changed

- Protocol trust is now explicit in the docs. `SECURITY.md` and
  `docs/protocol.md` state that `protocol.yml` is executable input, name
  `tooling.test_command` and `prepare_command` as shell-executed fields, and say
  plainly that snodo does not sandbox protocol-authored commands. Also cap
  `litellm` from `>=1.80.0` to `>=1.80,<2`; it is the widest-surface dependency
  on the coding and validation critical path and should not float across a major
  version boundary. (Fixes #110).

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

- Recovery specs now carry provenance for files written by earlier attempts in
  the same recovery chain. The spec distinguishes ownership from rewrite
  permission: superseded files are the coder's to remove, but listed files that
  are still needed or already correct must be left alone. This gives a recovery
  attempt the context needed to clean up orphaned earlier work without inviting
  churn against correct files. (Fixes #97).

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

- `LoopState` (and `LoopStage`) are defined exactly once. `engine/loop.py` and
  `engine/state.py` each defined a `LoopState` dataclass with the same fields,
  kept in sync by hand — the same defect class as #100, one layer down. The
  mixins in `engine/nodes/` import from `state.py` (the live one); `loop.py`'s
  copy was dead except for a `TYPE_CHECKING` import in `constraints.py` and two
  tests. #103 had to add its `base_ref` field to both definitions, and nothing
  would have failed if it had been added to only one. `loop.py` now re-exports
  `LoopState`/`LoopStage` from `state.py` instead of redefining them, and the
  MRO gate is extended to fail when the same class name is defined in more than
  one module under `snodo/engine/` — the general form of the check that would
  have caught this. (Fixes #107).

- Post-execute validators no longer review the previous commit when HEAD did
  not move. `llm_validator` and `acceptance` judged `git diff HEAD~1..HEAD`,
  and nothing established that HEAD moved during execute: an adapter that
  returned file operations but did not commit left `HEAD~1..HEAD` resolving to
  the previous unrelated commit, which the judges reviewed and passed.
  `InPlaceCoderAdapter._commit_changes` returns silently when the repo cannot
  be opened, `git add` fails, or nothing is staged, so the fault was not
  surfaced. The execute node now captures the HEAD sha before the coder runs
  (`GitMCP.get_head_sha`, carried on `LoopState.base_ref` through the langgraph
  dict serde and on `ValidatorContext.base_ref`), and the judges diff
  `base_ref..HEAD` instead — falling back to `HEAD~1..HEAD` only when the
  anchor is absent, with the prompt telling the judge the range is a fallback.
  If the executor returned artifacts but HEAD did not move, the engine now
  halts with the distinct `head_not_moved` halt type and a distinct
  `head_not_moved` audit op instead of passing on the wrong diff.
  `skip_engine_commit` / `skip_workspace_write` remain capability declarations;
  opting out no longer transfers an unenforced obligation. (Fixes #103).

- Audit log test isolation and distinct error reporting in `snodo merge`. Resolved audit log leakage into the suite repository during `pytest -n auto` by redirecting `get_audit_log()` default paths under `SNODO_HOME` when set and resetting the process-global `_global_audit_log` singleton in `conftest.py`'s `isolate_snodo_home` autouse fixture. `_record_merge_and_review` in `snodo merge` now explicitly distinguishes failure modes (audit chain corruption `AuditError`, file permission/IO error `OSError`, and project root resolution failure), surfacing loud, prominent diagnostic guidance for broken hash chains (`rm .snodo/audit.log` to start a clean chain) rather than masking all failures under a generic resolution warning. (Fixes #96).

- The project audit log resolves against the project root, not the user.
  `get_audit_log()` previously preferred `$SNODO_HOME/.snodo/audit.log` when
  `SNODO_HOME` was set — but `SNODO_HOME` is the `~/.snodo` equivalent
  (config, sessions, memory), user-scoped, not project-scoped. A user with
  `SNODO_HOME` set (supported for read-only-FS deployments) had every
  project's hash-chained audit log appended to one shared file, so interleaved
  projects corrupted each other's continuity — the same class of failure #96
  was fixing — and the project-scoped readers (`task_report`/`task_review`,
  the dashboard) disagreed with the home-scoped writer. The audit log is a
  property of the PROJECT: it is the tamper-evident history of what happened
  in a repository and must travel with that repository. The `SNODO_HOME`
  preference is reverted; the default is `.snodo/audit.log` against the
  project root. #96's actual goal — the test suite must not write into the
  repository's own `.snodo/audit.log` — is preserved by isolating the project
  root in tests and an explicit `SNODO_AUDIT_LOG` override used only by the
  test fixture, not by redirecting the default. `reset_global_audit_log()` and
  the conftest reset are kept. The e2e audit-log fixture now raises when the
  log file is missing (a missing log and an empty log are different facts).
  (Fixes #111).

  Note: `.snodo/audit.log.test-polluted` in this repository is the artifact of
  the original #96 bug (a test-time write that escaped into the suite repo's
  `.snodo/` before the isolation landed) and can be deleted.

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

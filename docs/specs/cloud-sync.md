# Cloud sync — the emit contract

What snodo sends when cloud sync is enabled, in what shape, and what it
deliberately never sends. This is the authoritative description of the wire.

The contract is 0.x and tracks `main`. It changes when snodo changes, without
notice and without a deprecation window. Treat it as a disclosure of what
leaves the machine, not as a stable integration surface.

Compiled against the code, not against intent. When this file and
`infrastructure/cloud_sync.py` disagree, the code is right and this file is a
bug.

Sync is opt-in: nothing is transmitted unless `cloud.sync_enabled` is true and
`cloud.api_key` is set. A run with sync disabled makes no network call.

## The wire

```
POST {lease_url}/m                  # mint a lease on the app host; no body
Authorization: Bearer <full cloud.api_key>

POST {api_url}/i/{jti}              # send audit batch on the API host
Authorization: Bearer <lease token>
```

With the defaults these are `https://app.snodo.dev/m` and
`https://api.snodo.dev/i/{jti}`. Mint returns `jti`, `token`, `expires_at`
(ISO 8601), and `cadence_s`; the client renews before `expires_at`. `jti` is
the lease identifier and is used in the ingest URL. A 401 from ingest causes
one re-mint and one retry. `cloud.lease_url` (or `cloud.lease_api_url`)
overrides the mint app host/base path; `cloud.api_url` sets the ingest host.
When only `cloud.api_url` is set, the mint host is derived by replacing its
`api` hostname label with `app` (or retaining a single-host custom/local host).

The successful lease-mint response also advertises the highest accepted
`interface_version`. Missing or invalid values mean v5. Snodo sends v6 event
types, v6-added event data, and v6 liveness sections only under a lease that
advertises version 6 or later. Until then, unchanged v5-compatible events are
sent; the cursor stops before the first v6-only event/data and its chain suffix
is retried after a later mint. Data is never stripped from a hash-chained event.
snodo-cloud must advertise v6 only after both ingest and liveness accept it.

Batched 1-50 events. Dispatched from a background thread during `snodo run`
teardown and from `snodo cloud sync`; nowhere else. The cursor advances only on
a 2xx, so a failed batch re-sends rather than being lost. Interface v6 is sent
only after the cloud accepts v6; until then the client continues sending v5.
New events are recorded locally while waiting. ("Nowhere else" scopes the
*ingest* path: liveness is a second, separate wire, described below.)

```json
{
  "session_id":   "sess_20260902_prod_363b8e",
  "project_path": "/absolute/path/to/project",
  "display_name": "nfc-card-v2",
  "events": [
    {
      "sequence":      15,
      "timestamp":     "2026-09-02T21:50:01.701669+00:00",
      "event_type":    "task_merged",
      "project_id":    "local:6bd1d012554546c4b9462bfaaa4183d8",
      "scope":         "local",
      "data":          {},
      "previous_hash": "9f2c...",
      "event_hash":    "sha256 over sequence|timestamp|event_type|project_id|data|previous_hash"
    }
  ]
}
```

Response handling: 2xx advances the cursor; 401 re-mints once; 429 respects
`Retry-After`; 5xx and network errors retry with exponential backoff up to five
times. A 404 is a route mismatch and remains retryable (it never marks a
session refused). Other 4xx, including batch-level 413/422, mark the session
refused and stop until `--force`; the URL, status, and server message are shown.
Admission errors also show the mint URL and response details. “Cloud admission
unreachable” is reserved for cases where no server response was received.

`project_id` is an input to `event_hash` and always has been. Transmitting it
therefore changes no hash and invalidates no chain — it was simply being
dropped by the transmit path until #202.

`display_name` is the project's directory basename. There is exactly one name
field on the envelope; an earlier draft of #202 also sent `project_name` with
the same value and it was removed. Consumers read `display_name`.

`scope` is transmitted alongside `project_id` on every event. It is derived
from the id itself (`scope_for_project_id`), so the two cannot disagree: a
`local:` id carries `"local"`, anything else carries `"remote"`. Its purpose is
to tell the consumer when *not* to reconcile — a `local` id is a leaf and must
never be merged with anything, however many clones report the same
`display_name`.

## Project identity

Identity has three states and one of them is deliberately a dead end.

| scope | id | meaning |
|---|---|---|
| `remote` | normalized git remote URL | The same project on every machine. Engineers working from one remote converge on one id. |
| `local` | `local:<uuid>` | A single checkout. **Intentionally unreconcilable** — nothing can establish that two local checkouts are the same project, across machines or across paths on one machine. |

Two `local:` projects sharing a display name are two projects. That is the
correct reading, not a duplicate to be merged.

Promotion is one-way: a project acquires a remote and its id becomes the remote
id. There is no demotion. Once established, project identity is stable: a cached
`remote` or `override` id is returned as-is. If a git remote is repointed or
changed, snodo warns on stderr that the identity is unchanged and that running
`snodo init` adopts the new remote if desired. It does not automatically change
the project id or split the audit history.
`get_project_id` re-resolves only cached `local:` identities; cached `remote`
and `override` identities are stable and returned without repeated subprocess
calls.

The decision governing all of this is documented in ADR 012 (`docs/decisions/012-project-identity-from-git-remote.md`).

## Liveness — what is running right now

HISTORY (the ingest path above) is append-only and outlives the machine.
LIVENESS is mutable and worthless once stale: "task 2.1 is in_progress" is
true for forty minutes and then false. So it travels a separate wire with
opposite mechanics (Fixes #291) — on the app host rather than the ingest host:
giving liveness the cursor's delivery guarantee would replay a stale status
after a failed push, which is worse than the gap it covered.

```
POST {liveness_url}/i/{jti}
Authorization: Bearer <lease token>
Content-Type: application/json
Body: full liveness snapshot including session_id
```

With defaults the client sends `POST https://app.snodo.dev/i/{jti}` with the
same lease minted at `POST https://app.snodo.dev/m`; audit ingest sends to
`https://api.snodo.dev/i/{jti}`. `cloud.api_url` configures ingest and derives
the app host; `cloud.liveness_url` overrides the liveness base independently.
The client renews near expiry and re-mints once on a 401. Its push cadence
follows the lease's `cadence_s` when available.

The machine pushes; nothing reaches inward. The tunnel remains a convenience,
not a requirement.

**Change-driven, with a floor while running.** A push starts when something
actually changes: a plan/task status write (planner `update_status`), or an
engine transition observed through the audit log of a running process
(`dispatch`, `transition`, `halt`, `task_complete`, `task_merged`,
`token_consumed`, `post_validation_route`, `verification_executed`,
`unverified_merge_blocked`, `execution_failed`, `session_started`,
`session_task_changed`). The task's final word is on the wire regardless of
when its `state.json` was last rewritten: every snapshot carries what the
last event was, and a terminal event (`halt`, `task_complete`, …) forces the
push past the throttle. But a run that is quiet is still a run: while a
session has work running, at least one push per interval is sent even when
nothing changed, so silence on the far side means the machine stopped rather
than that the session had nothing new to say (Fixes #323). The floor is not a
heartbeat — a repeat carries the same true snapshot and no state is
fabricated — and a session with nothing running still sends nothing: if no
plan has begun and there is no task or job record, the snapshot is not built.
Once every task and job has settled, the terminal push is the run's last
word and the floor stops.

**Two clocks, deliberately distinct** (Fixes #324). A plan/task status write
changes what is running *and* fires a push, but it appends no audit event — so
"the last audit event" is not "the last thing that happened", and a consumer
that conflates them renders a working machine as idle. The snapshot carries
both. `last_event` is the last recorded decision — event type and timestamp
from the audit tail, the governance log — and it does not move when a status
write happens without one. `last_activity_at` is when something last happened
in this session, across everything the snapshot reports: the newest of the
plan `status.json` and task/job `state.json` modification times and the last
audit event's own timestamp. Render "last activity" from `last_activity_at`;
read `last_event` for what was last decided. No audit event is appended to
move either clock, and neither is a heartbeat: with nothing changing, both
stand still.

**At most one push per interval per session, and at least one while
running**, so a burst of transitions coalesces into one write and a quiet
session is still heard from. The interval is 60 seconds by default and
configurable with `cloud.liveness_interval_seconds`; lowering it trades
bandwidth for freshness. The snapshot is built at send time inside the
worker, so the coalesced write carries the later state. A transition that
ends a run's claim to be live — a terminal audit event or a plan-task status
of `completed`/`blocked`/`errored`/`unmerged` — bypasses the throttle:
dropping the sole record of "this stopped" would strand a false "running"
until some later event displaced it.

**A full snapshot, never a delta, and it carries the plan's shape.** A lost
push is harmless; the next supersedes it entirely. A failed push is dropped —
no queue or cursor. `cloud_sync.json` records the last push attempt, last
liveness error and failure count independently of the audit cursor; the first
failure per run warns with URL, status and server message. Shape, not flat
lists (Fixes #303): plans arrive as plan → waves →
tasks → the jobs beneath them, the structure `snodo plan status` renders. A
branch that has completed — a wave whose every task has settled, a plan whose
every wave has — is carried as a count plus the summary needed to render
"wave 1: 3/3 done" (`total` and a per-status `status_counts`), not as a full
list of its members. A collapsed wave also carries `wave_ids`, the distinct
durable registry identifiers already carried by its tasks, when any exist. The
running frontier keeps its detail, because that is the part a viewer is
watching: unsettled waves enumerate their started tasks, and each enumerated
task carries its own `wave_id` when classified. Each task node also carries the
*live* jobs beneath it. Terminal jobs inside an
incomplete plan stay in the picture as the plan's `job_status_counts`. Task or
job records that no plan claims ride the top level: live ones enumerated in
full, settled ones tallied in `task_status_counts` / `job_status_counts`.
Nothing still running is ever reduced to a count or hidden by the collapsing;
a live record whose plan node settled is enumerated. What is sent grows with
the plan's shape and what is in flight, never with elapsed time — a session
kept open for weeks re-ships its settled history as digits, not as lists.

**The key is the session.** A session is already project-scoped, persisted,
and survives a restart (a resumed run rejoins it), so it accumulates no rows
the way an instance id would. The project rides along so the far side can join
on it; machine identity adds nothing a session does not already imply. No user
identifier is sent — the credential identifies the person, and attribution is
taken from it on the far side so a sender cannot claim to be someone else.

```json
{
  "session_id":     "sess_20260902_prod_363b8e",
  "project_id":     "local:6bd1d012554546c4b9462bfaaa4183d8",
  "scope":          "local",
  "display_name":   "nfc-card-v2",
  "run_started_at": "2026-09-02T21:10:04+00:00",
  "plans": [
    {
      "name": "wave8",
      "total": 6,
      "status_counts": {"completed": 4, "in_progress": 1, "pending": 1},
      "job_status_counts": {"completed": 8},
      "waves": [
        {"id": 1, "total": 3, "status_counts": {"completed": 3},
         "wave_ids": ["w_0009"]},
        {
          "id": 2, "total": 3,
          "status_counts": {"completed": 1, "in_progress": 1, "pending": 1},
          "tasks": [
            {"id": "2.1", "status": "completed", "wave_id": "w_0009"},
            {
              "id": "2.2", "status": "in_progress",
              "started_at": "2026-09-02T21:10:05+00:00",
              "jobs": [
                {"id": "j_20260902_211005_a1b2", "status": "running",
                 "started_at": "2026-09-02T21:10:06+00:00"}
              ]
            }
          ]
        }
      ]
    }
  ],
  "tasks": [
    {"id": "implement-oob", "status": "running", "started_at": "2026-09-02T21:10:05+00:00"}
  ],
  "jobs": [
    {"id": "j_20260902_211100_c3d4", "status": "running", "started_at": "2026-09-02T21:11:00+00:00", "task_ref": "implement-oob"}
  ],
  "task_status_counts": {"completed": 12, "failed": 1},
  "job_status_counts": {"completed": 40, "failed": 2},
  "last_event":  {"event_type": "dispatch", "timestamp": "2026-09-02T21:50:01.7+00:00"},
  "last_activity_at": "2026-09-02T21:50:02.5+00:00",
  "snapshot_at": "2026-09-02T21:50:02.9+00:00"
}
```

The plan ordinal (`waves[].id`, such as `1` or `2`) and the registry identifier
(`wave_id` / `wave_ids`, such as `w_0009`) are different groupings. The ordinal
means execution order and membership in this plan; the registry identifier is
assigned independently per task by classification and may be shared by tasks
in different plan waves. One plan wave can therefore carry several registry
identifiers, or none. Consumers must join history to liveness through the
task-level `wave_id`, or a settled wave's `wave_ids`, not by treating the plan
ordinal as a durable wave identifier.

Statuses are the ones the machine already records — planner statuses, task
and job `state.json` statuses — passed through verbatim; this path adds no
status, halt type or state value. Counts (`total`, `status_counts`,
`job_status_counts`, `task_status_counts`) tally those existing statuses;
they introduce no vocabulary. Only *when* and *how* they travel is changing.
A job is linked to its plan task by the task identity the engine already
records for the job; the link's own payload fields (the spec, the
description) stay on the disk. `started_at` values (epoch on disk) are
normalised to ISO. `run_started_at` is the session file's `created_at`.
`display_name` is the project directory basename. `last_activity_at` is ISO
too — the newest of the record-file modification times and the `last_event`
timestamp — so a status write that appends no audit event still moves it.

What the liveness wire never carries is what the ingest path never carries:
payloads, prompts, diffs, file contents, absolute paths, `usage` records, halt
payloads — and, unlike the ingest envelope, no `project_path`. Opt-in is the
same single gate: `cloud.sync_enabled` off or no `cloud.api_key` means a run
makes no network call on this path either. Log streaming is out of scope.

Every long-lived process reports liveness: `snodo run`, the MCP server and
`snodo queue run`. Recon entries are included in the full snapshot while their
process is running (id, question excerpt, agent count and start time); a recon
whose process is gone is treated as stale and omitted. These entries describe
currently running work, not recon history.

## What is in `data`

Everything the event carries, verbatim. That is the whole audit payload, so it
includes task specs, validator justifications quoting file contents, shell
commands, absolute working directories and captured test output. "Opaque" is
not an adequate description of a field that leaves the machine; the table below
is.

Every audit event type emitted by snodo is declared and transmitted; the
event tag is part of the hash chain and no event is skipped client-side. The
ingest service must accept interface v6 before the client sends the v6
contract; until acceptance, the client sends v5. Data for
some tags is intentionally an opaque JSON object (`additionalProperties` is
allowed): the event type is pinned on the wire even when its data shape is not.
This expands what the cloud receives, but creates no engine state, severity,
halt type or status value. The existing never-transmitted rules below still
apply.

The v6 additions include `recon_started` (the full recon question and run
metadata) and `recon_completed` (the existing final status, agent outcome
counts, duration and answer summary capped at about 2,000 characters with a
truncation marker). Thus, when `cloud.sync_enabled` is on, recon questions and
answer summaries now leave the machine in audit events. `task_merged` also
includes the pre-merge base SHA, full commit count, up to 50 commit SHAs and
subjects (with a truncation marker when capped), changed-file count, insertions
and deletions. Measurement is best-effort; diffs, file contents and commits
outside snodo's merge range are not sent. Task events carry `plan_name` and the
plan's `plan_wave` when applicable. `plan_proposed` and `plan_run` carry the
pinned hierarchy `{plan_name, waves: [{wave_id, task_refs}]}`; plan runs also
identify their trigger and, for queue-triggered runs, the queue.

| event_type | data keys |
|---|---|
| `project_announced` | project_id, scope, display_name |
| `readiness_checked` | project_id, scope, display_name, protocol_id, score, total_checks, passed_checks, repository_findings_count, workstation_findings_count, findings |
| `recon_started` | recon_id, query, paths, agent_count, agent_models, session_id, created_at |
| `recon_completed` | recon_id, existing final status, succeeded_agents, failed_agents, duration, completed_at, answer summary (about 2,000 characters maximum) |
| `dispatch` | task_ref, mode, token_id, artifacts_count, plan_name, plan_wave (when applicable) |
| `work_already_present` | task_ref, base_ref, artifacts_count, files |
| `governance_check` | task_ref, mode, constraints_checked |
| `validate` | phase, task_ref, validators_invoked, results, outcome, policy_decision |
| `task_classified` | task_ref, flow_type, wave_id, task_summary, plan_name, plan_wave (when applicable) |
| `wave_created` | wave_id, feature_description |
| `task_complete` | task_ref, artifacts, session_id, commit, change_size, plan_name, plan_wave (when applicable) |
| `task_merged` | task_ref, branch, merge_sha, spec, session_id, base_sha, commit_count, commits (up to 50), files_changed, insertions, deletions, plan_name, plan_wave (when applicable) |
| `halt` | task_ref, reason, blocker_validators, halt_type, raw_halt_type, plan_name, plan_wave (when applicable) |
| `transition` | from_mode, to_mode, task_ref |
| `token_consumed` | task_ref, session_id |
| `post_validation_route` | decision, task_ref |
| `post_validate_bypassed` | mode, reason, task_ref |
| `session_started` | session_id, mode, project_root |
| `session_task_changed` | old_task, new_task |
| `session_decision_updated` | key, value |
| `recovery_resolved` | depth, attempts_used |
| `recovery_internal_error` | depth, error |
| `execution_failed` | error, task_ref |
| `verification_executed` | command, commit, returncode, outcome, validator_id, working_directory, output_tail |
| `coder_test_run` | command_type, exit_code, test_path, turn_index, job_id |
| `test_modified` | mutations, task_id, job_id |
| `unverified_merge_blocked` | task_ref, branch, target_commit, reason, session_id |
| `adjudication_carry_forward` | opaque object |
| `coder_respawned` | opaque object |
| `coder_timed_out` | opaque object |
| `coder_turn_budget_exhausted` | opaque object |
| `coder_unavailable` | opaque object |
| `decision_record_task_mismatch` | opaque object |
| `disagreement_escalated` | opaque object |
| `disagreement_resolved` | opaque object |
| `dispatch_refused_coder_unavailable` | opaque object |
| `dispatch_request` | opaque object |
| `environment_prep_failed` | opaque object |
| `head_not_moved` | opaque object |
| `human_review_recorded` | opaque object |
| `job_state_corrupt` | opaque object |
| `merge_conflict_escalated` | opaque object |
| `merge_failed_escalated` | opaque object |
| `mode_change` | opaque object |
| `no_file_operations` | opaque object |
| `plan_proposed` | plan_name, waves (wave_id, task_refs) |
| `plan_run` | plan_name, waves (wave_id, task_refs), trigger, queue (for queue trigger) |
| `protected_path_blocked` | opaque object |
| `protected_paths_unchecked` | opaque object |
| `recovery_exhausted` | opaque object |
| `recovery_stalled` | opaque object |
| `session_audited_but_missing` | opaque object |
| `session_corrupt` | opaque object |
| `session_deleted` | opaque object |
| `session_memory_updated` | opaque object |
| `session_pointer_audited_but_missing` | opaque object |
| `session_resumed` | opaque object |
| `severity_cap_applied` | opaque object |
| `snodo_mutation_blocked` | opaque object |
| `spec_authored` | opaque object |
| `spec_authored_failed` | opaque object |
| `spec_premise_stale` | opaque object |
| `subtask_spawned` | opaque object |
| `task_add_rejected` | opaque object |
| `task_added` | opaque object |
| `task_replaced` | opaque object |
| `task_status_corrected` | opaque object |
| `task_unmerged` | opaque object |
| `token_store_unavailable` | opaque object |
| `tool_call` | opaque object |
| `validator_contradiction_detected` | opaque object |
| `validator_results` | opaque object |
| `wf3_runtime_violation` | opaque object |
| `worktree_isolation_failed` | opaque object |

The schema gate scans literal calls to `append_event()` and `_audit()` under
`packages/` and `snodo/`; a newly emitted literal event type must be added to
the contract before the gate passes. All `data` values remain JSON objects, and
the ingest service must be updated to accept these additional event tags.

`session_id` is injected into every engine event by `_audit()`, so it is
present on events whose call site does not name it.

`task_complete.commit` is the commit the completed task produced, or `null`.
An explicit null is the honest answer for a task that completed without
committing — a documentation task, an investigation, a no-op outcome — and is
deliberately distinguishable from an older event recorded before the field
existed, which carries no key at all.

`task_complete.change_size` records how much the task changed and the
repository-relative paths it covered, without recording what was in them:
line totals and per-shape file counts of the task branch against
its own branch point — the merge-base of the resolved base branch and the
branch, never the base branch's current tip, so a long-running task is
never credited with work others merged while it ran. The diff itself is
never recorded; only counts leave the machine. A `null` `change_size` is
the honest absence of a measurement (no repository, no task branch, an
unresolvable base), never a fabricated zero. Inside the record:
`lines_added`/`lines_deleted` count only lines that exist to be counted; a
binary file carries no line count and appears in `files_binary`, so a
zero-line binary change is distinguishable from a zero-line text change;
renames appear in `files_renamed` (line totals still reflect any content
edit they carry); a mode-only change moves no line and appears in
`files_mode_only`; a deletion's removed text lines land in
`lines_deleted`. Past `CHANGE_SIZE_MAX_FILES` changed files the line
totals are not computed at all — `lines_added`/`lines_deleted` are `null`
and `capped` is true — because a plan run must never stall on a statistic
nobody is waiting for; `files_changed` stays real. `paths` contains every
changed path when the comparison is measured. When `capped` is true, it
contains only the bounded prefix and is therefore incomplete; `files_changed`
remains the real total. The interface version moved to 4 for this field.

`readiness_checked.findings` carries repository method scaffolding findings
with relative paths only and never workstation detail (such as local binaries on
`PATH` or environment variables). Workstation prerequisites are recorded in
`workstation_findings_count` only, ensuring that audit logs from different
machines remain consistent for the same repository state.

`work_already_present` is emitted when a retry finds the task's work already
committed on its task branch — the coder wrote nothing this attempt, but the
branch is ahead of its base — and carries it into post-execute validation
instead of halting as `no_file_operations`. `base_ref` is the branch base the
post-execute judges diff against (`base_ref..HEAD`). `files` lists the produced
file paths and carries repository-relative paths only, never absolute paths or
contents.

`verification_executed.outcome` is `"pass"`, `"fail"`, `"error"`, or
`"no_tests"`. `"no_tests"` is the honest record of an ungated run: the shipped
no-op default test command ran and no tests were executed (no
`tooling.test_command` configured and no marker file detected). A consumer
counting ungated projects or distinguishing a real pass from a placeholder
must key on that outcome — an ungated run never carries `"pass"` (ADR 031).

When a quality run fails, it may rerun only the reported pytest failure node
ids in a detached worktree at the task's base commit. If those same tests fail
there, the failure is reported as pre-existing and remains a blocking,
fail-closed validator error; it is not sent through task recovery and is not
silently converted to a pass. If the baseline rerun passes, the original
failure remains blocking and is treated as a task failure for routing. This is
also the flaky-test rule: a passing rerun never clears a failure unless the
failure is reproduced at the base commit.

`project_announced` is emitted on session creation (`SessionManager.create_session`)
and on session resume (`_resolve_session` in `run_cmd` when adopting an existing
session or explicitly resuming; Fixes #214, #219), where the project identity is
already resolved. It carries the resolved `project_id`, its `scope`, and the
`display_name` (the project's directory basename) so a consumer reading only the
event stream can create the project row without inspecting the transport
envelope. It is deliberately re-emitted on every run — a consumer that joined late,
lost a batch, or rebuilt from an advanced cursor still sees a project that is being
worked on. The payload is identity, scope and display name only: no paths, no machine
details. `display_name` remains on the sync envelope for now; removing it is a
separate wire-trim.

`flow_type` and `wave_id` are emitted on `task_classified`. When they are
absent it is because the classifier failed, not because they are unplumbed —
the run prints "Classifier failed after N attempts, leaving task unwaved".
`wave_created` is emitted once when a wave is created, carrying `wave_id` and
`feature_description`.

`halt` distinguishes *why* a task stopped and *who* stopped it. `halt_type` is
the canonical five-outcome name (`escalate`, `blocker`, `validator_error`,
`internal_error`, `environment_error`; ADR 015); `raw_halt_type` is the specific
value the loop actually set
(e.g. `turn_budget_exhausted`, `recovery_stalled`), preserved next to the
canonical one so the coarse outcome never erases the precise cause.
`blocker_validators` names the judges that returned a blocking verdict. A judge
that could not reach a verdict is an operational error (``error=True``) and is
counted there like any other blocker; there is no separate no-verdict list.
The `halt` event carries a single outcome field
(`halt_type`); the legacy duplicate `final_decision`, which always equalled
`halt_type`, was retired from the event once consumers read `halt_type`
(the persisted job/session halt *payload* still carries it and is a different
shape, not this wire event).

## session_decision_updated

Roughly three quarters of everything transmitted, and mostly restatement. The
event ships the whole sub-dictionary for whichever key changed, re-sent on
every write. Measured over one small session:

| key | events | value bytes | disposition |
|---|---:|---:|---|
| `halt` | 20 | 460,509 | Remove. A snapshot of every halt in the session; per-task halts arrive as `halt`. |
| `classification` | 20 | 17,149 | Remove. Duplicate of `task_classified`. |
| `task_failure` | 12 | 14,150 | Trim to task_ref, attempt, branch, reason. |
| `pending_decisions` | 6 | 1,757 | Keep. Escalations awaiting `snodo authorize`, emitted nowhere else. |

Consumers should not build on the `halt` or `classification` keys.

## Planned changes

Coming off the wire:

- `session_decision_updated` keys `halt` and `classification`.
- `verification_executed.output_tail` — 400 characters of unfiltered test
  output, the highest-risk field on the wire.
- Absolute paths carrying the operator's home directory: envelope
  `project_path`, `session_started.project_root`,
  `verification_executed.working_directory`. Now redundant beside `project_id`
  and `display_name`.
- Raw exception text in the `error` fields.

Coming onto the wire:

- Protocol identity: `protocol_id` and a content hash, never the file itself.
  A receiver can currently see which validators ran but not which the protocol
  declared.
- Cost and duration per task, aggregated. `.snodo/tasks/<id>/state.json` holds
  usage and, since #69, an attribution record for in-place coder runs.

## Never transmitted

Not omissions — stated non-goals. A change that would send any of these needs
to argue against this list.

- `config.yml` — live provider and cloud credentials in plaintext.
- `checkpoints.db` — serialized graph state including full prompts.
- `tokens.db` — the single-use token ledger; consumption is attested by
  `token_consumed` events instead.
- Job `stdout.log` and `stderr.log` — unbounded, unredacted toolchain output.
- File contents and diffs. snodo transmits what happened, never the code it
  happened to.
- The RS256 signing key, which lives outside the project tree entirely.

## Where the code is

| Concern | Location |
|---|---|
| Cursor state | `infrastructure/cloud_sync.py` — `CloudSyncState`, `~/.snodo/cloud_sync.json` |
| Batching, retry, refusal | `infrastructure/cloud_sync.py` — `CloudSyncDispatcher` |
| Payload construction | `CloudSyncDispatcher._post_batch` |
| Liveness snapshot, throttle, push | `infrastructure/cloud_liveness.py` |
| Liveness triggering from engine events | `infrastructure/audit.py` — `register_event_listener` |
| Run-teardown hook | `cli/commands/run_cmd.py` |
| Connect / disconnect / status / sync | `cli/commands/cloud_cmd.py` |
| Config schema | `snodo/config.py` — `cloud.api_key`, `cloud.api_url`, `cloud.lease_url`, `cloud.sync_enabled`, `cloud.liveness_interval_seconds` |

## History

This file began as the implementation ticket for `snodo cloud connect` and
audit sync — config schema, cursor, dispatcher, retry policy, and the
acceptance criteria for each. That work shipped, and those behaviours are now
pinned by tests rather than by prose, so the ticket has been replaced by the
contract it produced. The retry and cursor semantics described above are
unchanged from the original specification.

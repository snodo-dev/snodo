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
POST {api_url}/ingest
Authorization: Bearer <account key>
```

Batched 1-50 events. Dispatched from a background thread during `snodo run`
teardown and from `snodo cloud sync`; nowhere else. The cursor advances only on
a 2xx, so a failed batch re-sends rather than being lost.

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
      "data":          {},
      "previous_hash": "9f2c...",
      "event_hash":    "sha256 over sequence|timestamp|event_type|project_id|data|previous_hash"
    }
  ]
}
```

Response handling: 200 advances the cursor; 429 backs off by `retry_after`; 5xx
retries with exponential backoff up to five times; any other 4xx marks the
session refused and stops until `--force`.

`project_id` is an input to `event_hash` and always has been. Transmitting it
therefore changes no hash and invalidates no chain — it was simply being
dropped by the transmit path until #202.

`display_name` is the project's directory basename. There is exactly one name
field on the envelope; an earlier draft of #202 also sent `project_name` with
the same value and it was removed. Consumers read `display_name`.

## Project identity

Identity has three states and one of them is deliberately a dead end.

| scope | id | meaning |
|---|---|---|
| `remote` | normalized git remote URL | The same project on every machine. Engineers working from one remote converge on one id. |
| `local` | `local:<uuid>` | A single checkout. **Intentionally unreconcilable** — nothing can establish that two local checkouts are the same project, across machines or across paths on one machine. |

Two `local:` projects sharing a display name are two projects. That is the
correct reading, not a duplicate to be merged.

Promotion is one-way: a project acquires a remote and its id becomes the remote
id. There is no demotion.

`scope` is not currently transmitted. It should be, and its purpose is to tell
the consumer when *not* to reconcile.

Known defect: `get_project_id` reads the `.snodo/project.json` cache before
resolving, so a repository initialised before it had a remote keeps its
`local:` id even once the remote exists. The project becomes globally
identifiable and its id does not follow.

The decision governing all of this is cited in `project.py` and `audit.py` as
ADR 012, which was never written.

## What is in `data`

Everything the event carries, verbatim. That is the whole audit payload, so it
includes task specs, validator justifications quoting file contents, shell
commands, absolute working directories and captured test output. "Opaque" is
not an adequate description of a field that leaves the machine; the table below
is.

All 21 event types are transmitted.

| event_type | data keys |
|---|---|
| `dispatch` | task_ref, mode, token_id, artifacts_count |
| `governance_check` | task_ref, mode, constraints_checked |
| `validate` | phase, task_ref, validators_invoked, results, outcome, policy_decision |
| `task_classified` | task_ref, flow_type, wave_id, task_summary |
| `task_complete` | task_ref, artifacts, session_id |
| `task_merged` | task_ref, branch, merge_sha, spec, session_id |
| `halt` | task_ref, reason, blocker_validators, halt_type, final_decision, raw_halt_type |
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

`session_id` is injected into every engine event by `_audit()`, so it is
present on events whose call site does not name it.

`flow_type` and `wave_id` are emitted on `task_classified`. When they are
absent it is because the classifier failed, not because they are unplumbed —
the run prints "Classifier failed after N attempts, leaving task unwaved".

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

- `scope`, alongside `project_id`.
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
| Run-teardown hook | `cli/commands/run_cmd.py` |
| Connect / disconnect / status / sync | `cli/commands/cloud_cmd.py` |
| Config schema | `snodo/config.py` — `cloud.api_key`, `cloud.api_url`, `cloud.sync_enabled` |

## History

This file began as the implementation ticket for `snodo cloud connect` and
audit sync — config schema, cursor, dispatcher, retry policy, and the
acceptance criteria for each. That work shipped, and those behaviours are now
pinned by tests rather than by prose, so the ticket has been replaced by the
contract it produced. The retry and cursor semantics described above are
unchanged from the original specification.

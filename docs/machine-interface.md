# Machine interface

Snodo's first committed machine interface (ADR 022). This is a **contract**, not
a convenience: field names and exit codes are stable, and a breaking change
bumps the schema version so a consumer can detect it before parsing.

## Conventions

- `--json` is **additive**. Human output is unchanged; `--json` only changes
  what is written to stdout.
- Every `--json` command writes a **single JSON object** to stdout. Errors are
  written to stderr, never stdout, so stdout is always one parseable document.
- Every payload carries a `schema` field of the form `snodo.<command>.v<N>`.
  A consumer must check this field first; a mismatch means the payload shape
  changed and the consumer should refuse to parse rather than misread it.
- Field names are asserted by the test suite. A rename fails the suite rather
  than a downstream consumer.

## Commands

### `snodo status --json`

Schema: `snodo.status.v1`

| Field | Type | Meaning |
|-------|------|---------|
| `schema` | string | `snodo.status.v1` |
| `ok` | bool | always `true` on success |
| `project_root` | string | absolute project root |
| `protocol` | object | `{id, name}` from `protocol.yml` |
| `mode` | string \| null | active mode id, or `null` |
| `active_session` | string \| null | active session id, or `null` |
| `last_run` | object \| null | `{session_id, mode, updated_at, outcome}` |

### `snodo mode show --json`

Schema: `snodo.mode.v1`

| Field | Type | Meaning |
|-------|------|---------|
| `schema` | string | `snodo.mode.v1` |
| `ok` | bool | always `true` |
| `mode` | string \| null | active mode id, or `null` |
| `name` | string \| null | display name, or `null` |
| `active_session` | string \| null | active session id for this mode |

### `snodo session show <id> --json`

Schema: `snodo.session.v1`

| Field | Type | Meaning |
|-------|------|---------|
| `schema` | string | `snodo.session.v1` |
| `ok` | bool | `true` on success |
| `session_id` | string | session id |
| `mode` | string | session mode |
| `project_root` | string | project root |
| `project_id` | string | project identity |
| `created_at` | string | ISO timestamp |
| `updated_at` | string | ISO timestamp |
| `checkpoint` | object | `{current_task, decisions, memory_summary}` |

### `snodo task show <id> --json`

Schema: `snodo.task.v1`

| Field | Type | Meaning |
|-------|------|---------|
| `schema` | string | `snodo.task.v1` |
| `ok` | bool | `true` on success |
| `task_id` | string | task id |
| `session_id` | string | session holding the record |
| `mode` | string | session mode |
| `halt` | object \| null | the halt payload, or `null` |
| `failure` | object \| null | the failure context, or `null` |

### `snodo worktree list --json`

Schema: `snodo.worktree.v1`

| Field | Type | Meaning |
|-------|------|---------|
| `schema` | string | `snodo.worktree.v1` |
| `ok` | bool | always `true` |
| `project_root` | string | project root |
| `worktrees` | array | `[{task_id, path, age_days}]` |

### `snodo monitor --json`

Schema: `snodo.monitor.v1`

A single read-only snapshot of what is on disk (audit log tail, task/job
`state.json`, worktrees). The live view refreshes on a timer; `--json` emits
one snapshot and exits.

| Field | Type | Meaning |
|-------|------|---------|
| `schema` | string | `snodo.monitor.v1` |
| `ok` | bool | always `true` |
| `project_root` | string | absolute project root |
| `now` | number | snapshot wall clock (epoch seconds) |
| `runs` | array | `[{kind, run_id, status, pid, alive, phase, idle_seconds, usage_records, cost_total, cost_partial, tokens_total, description, coder, state_readable}]` |
| `recent_events` | array | last phase events: `{ts, label, task_ref, detail, sequence}` |
| `worktrees` | array | `[{task_id, path, branch}]` |
| `notes` | array | reader caveats: unreadable/partial state, torn audit tail |

`alive` is `null` when no pid was recorded — liveness is then unanswerable and
the view says so rather than guessing. `idle_seconds` is the seconds since the
newest sign of life (audit event or per-call usage record), the number that
separates a slow phase from a dead run.

### `snodo validate <task_spec> [--phase pre_execute|post_execute] [--mode <m>]`

Schema: `snodo.validate.v1`

Runs the phase's validators through the shared engine runner and returns the
four-outcome result **without running a coder**. The shape mirrors the engine's
halt payload.

| Field | Type | Meaning |
|-------|------|---------|
| `schema` | string | `snodo.validate.v1` |
| `ok` | bool | `true` on success |
| `status` | string | `pass` \| `escalate` \| `blocker` \| `validator_error` |
| `task_id` | string | derived task id |
| `phase` | string | the phase validated |
| `mode` | string | the mode validated |
| `results` | array | `[{validator_id, severity, justification}]` |
| `policy_decision` | object \| null | the policy decision |
| `instruction` | string | follow-up instruction |

## Exit codes

`snodo validate` (and any command that returns a validation outcome) uses exit
codes that distinguish the four outcomes, so a caller can branch without
parsing prose:

| Exit code | Outcome |
|-----------|---------|
| 0 | `pass` |
| 1 | `blocker` |
| 2 | `escalate` |
| 3 | `validator_error` |
| 4 | `internal_error` |

## Error shape

When a `--json` command cannot produce its normal payload (not inside a
project, missing argument, unknown id), it emits a uniform error object and a
non-zero exit code:

```json
{
  "schema": "snodo.<command>.v1",
  "ok": false,
  "error": "human-readable reason"
}
```

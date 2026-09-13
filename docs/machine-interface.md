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
| `spec` | string \| null | the task spec |

For a task that is **still running** (no halt/failure record yet, a live run
record on disk) the command answers `ok: true` with `status: "running"`, null
`halt`/`failure`, and a `watch` field pointing at the live surface rather than
a record. This distinguishes a running task from an unknown one, which keeps
returning the `No record for task <id>` error.

### `snodo worktree list --json`

Schema: `snodo.worktree.v1`

| Field | Type | Meaning |
|-------|------|---------|
| `schema` | string | `snodo.worktree.v1` |
| `ok` | bool | always `true` |
| `project_root` | string | project root |
| `worktrees` | array | `[{task_id, path, age_days}]` |

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

### `snodo survey --json`

Schema: `snodo.survey.v1`

Reports one repository in one of two shapes, depending on whether a protocol
already exists. The `analysis` object is the same in both; `analysis.drift` is
present **only** for a repository that already has a protocol.

| Field | Type | Meaning |
|-------|------|---------|
| `schema` | string | `snodo.survey.v1` |
| `ok` | bool | `true` on success |
| `project_root` | string | absolute project root |
| `project_id` | string | project identity |
| `scope` | string | `remote` \| `local` \| `override` |
| `display_name` | string | repository directory name |
| `analysis` | object | discovered modules, languages, tooling, decision records, test command, findings, agent judgements and the judgements not made |
| `analysis.drift` | object | **governed repositories only** — what the protocol claims against what the code shows |

`analysis.drift`:

| Field | Type | Meaning |
|-------|------|---------|
| `protocol_id` | string | the protocol surveyed |
| `uses_modules` | bool | whether the protocol declares `modules:` at all |
| `shape` | string | which shape the report is reading, in prose |
| `summary` | string | one-line account of what was weighed |
| `checks_made` | int | comparisons that were actually carried out (agreements + divergences) |
| `has_divergences` | bool | whether any comparison diverged |
| `agreements` | array | `[{check, statement, subject, evidence}]` — comparisons that agreed |
| `divergences` | array | `[{check, subject, claim, observation, evidence}]` — where the two differ; `claim` restates the protocol, `observation` restates the code, and neither names a fault |
| `not_compared` | array | `[{check, reason}]` — comparisons this protocol's shape does not support, and why |

`check` is one of `protocol-module-paths`, `module-coverage`,
`decision-records`, `test-commands`, `validator-tooling`. A protocol that
declares no modules (the common shape) gets no module-level comparisons at all:
the three module checks appear in `not_compared` with their reason, and no
discovered boundary is reported as ungoverned.

`snodo survey` **exits 0 whenever it produced a report**, including a report
that found drift: a comparison that diverged is a true observation, not a
judgement that either side is at fault, and survey adjudicates nothing. A
caller distinguishes the outcomes from the payload, not the exit code:

| Situation | Exit | Payload |
|-----------|------|---------|
| ungoverned repository | 0 | `analysis` without a `drift` field |
| governed, no divergence | 0 | `analysis.drift.has_divergences: false` |
| governed, divergence found | 0 | `analysis.drift.has_divergences: true` |
| no git repository, or a protocol that will not load | 4 | `ok: false`, `error` |

Code 4 therefore means "no report was produced" and nothing else. Surveying a
governed repository is not an error state, and `1`/`2`/`3` are validation
outcomes that survey does not emit: survey proposes and reports, it does not
judge work.

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

No command adds a sixth code. `snodo survey` deliberately uses none of the
judgement codes (1–3): it adjudicates nothing, so a governed repository whose
protocol has diverged from its code exits **0** like any other successful run
and reports the divergence in its payload
(`analysis.drift.has_divergences`). A non-zero exit from survey means no report
was produced at all — see `snodo survey --json` above.

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

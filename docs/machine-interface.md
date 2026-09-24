# Machine interface

Snodo's first committed machine interface (ADR 022). This is a **contract**, not
a convenience: field names and exit codes are stable, and a breaking change
bumps the schema version so a consumer can detect it before parsing.

## Conventions

- `--json` is **additive**. Human output is unchanged; `--json` only changes
  what is written to stdout.
- Every `--json` command writes a **single JSON object** to stdout. Errors are
  emitted to stdout as that object, so stdout is always one parseable document.
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

The `halt` payload carries an `attempts` summary alongside the final
`validator_results`: `total`, `coder_dispatches`, and a bounded `history` of
`{attempt, outcome}` entries (ADR 045). A consumer can therefore tell a
first-time pass from a hard-won one, and count coder dispatches, without
parsing prose. The summary is additive to the payload.

### `snodo worktree list --json`

Schema: `snodo.worktree.v1`

| Field | Type | Meaning |
|-------|------|---------|
| `schema` | string | `snodo.worktree.v1` |
| `ok` | bool | always `true` |
| `project_root` | string | project root |
| `worktrees` | array | `[{task_id, path, age_days}]` |

### `snodo queue remove <plan> --json`

Schema: `snodo.queue.remove.v1`

| Field | Type | Meaning |
|-------|------|---------|
| `schema` | string | `snodo.queue.remove.v1` |
| `ok` | bool | `true` when the plan was removed |
| `plan` | string | Removed plan name |
| `queue` | string | Queue the plan was removed from |

### `snodo validate <task_spec> [--phase pre_execute|post_execute] [--mode <m>]`

Schema: `snodo.validate.v1`

Runs the phase's validators through the shared engine runner and returns the
validation-outcome result **without running a coder**. The shape mirrors the
engine's halt payload. The engine's canonical vocabulary is five (ADR 015);
`environment_error` is an execution halt, and because this command never
invokes a coder, it is not one of the outcomes returned here.

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

### `snodo intake --json`

Schema: `snodo.intake.v1`

Reports the validator criteria `snodo intake` would offer, drawn from the
repository's decision records. It **writes nothing** and prompts for nothing —
a machine can see the proposals, but acceptance stays a human act; the write
path is entered only by the interactive command or an explicit `--accept-all`.

| Field | Type | Meaning |
|-------|------|---------|
| `schema` | string | `snodo.intake.v1` |
| `ok` | bool | `true` on success |
| `project_root` | string | absolute project root |
| `protocol_id` | string | the protocol the criteria would be added to |
| `proposals` | array | `[{criterion, record_path, record_title, record_section}]` — each rule and the record it came from |
| `written` | bool | always `false` |

`record_path` is a repository-relative citation resolved against the
repository before the proposal is built; a criterion whose record cannot be
found is never proposed. A non-zero exit from `intake --json` means no
proposals could be produced (no git repository, no protocol, or an unloadable
one), and it writes nothing.

### `snodo models --json`

Schema: `snodo.models.v1`

Lists discovered models without the human table. With no `--provider`, the
`models` array is empty and `providers` lists configured providers with usable
credentials. With a provider, `models` contains the discovery records; filters
are applied before the payload is emitted.

| Field | Type | Meaning |
|-------|------|---------|
| `schema` | string | `snodo.models.v1` |
| `ok` | bool | `true` on success |
| `provider` | string \| null | selected provider, or `null` when listing providers |
| `providers` | array | configured providers when no provider was selected |
| `models` | array | discovered model records, or `[]` when none match |

### `snodo models --stats --json`

Schema: `snodo.models-stats.v1`

Returns recorded usage aggregates without the human tables. Raw duration arrays
and call counts remain available so a consumer can choose its own aggregation.

| Field | Type | Meaning |
|-------|------|---------|
| `schema` | string | `snodo.models-stats.v1` |
| `ok` | bool | `true` when the stats report was produced |
| `project_root` | string \| null | project whose records were read |
| `provider` | string \| null | provider filter, if supplied |
| `total_jobs` | int | jobs found in the project |
| `models` | object | model id to usage record, including calls, token totals, durations, costs and roles |
| `coders` | object | coder id to job totals and durations |

### `snodo runs --json`

Schema: `snodo.runs.v1`

Emits the accumulated completed task run records. A task is one row; background
job copies are not emitted as additional rows. Measurements that were not
recorded are omitted, while measured zeroes remain zeroes.

| Field | Type | Meaning |
|-------|------|---------|
| `schema` | string | `snodo.runs.v1` |
| `ok` | bool | `true` when the report was produced |
| `project_root` | string | absolute project root |
| `runs` | array | completed run records, each with `task_id`, `completed_at`, and recorded `cost` fields |

### `snodo models --benchmark --json`

Schema: `snodo.models-benchmark.v1`

Runs the same fixed prompt and performs the same measurements as human mode,
but emits one JSON object instead of the intent and report prose. A failed API
call is still a sample, so `attempted_runs` can exceed `succeeded_runs`.
Aggregates in `statistics` cover successful samples only; consumers that need
to combine runs retain the individual `samples`.

| Field | Type | Meaning |
|-------|------|---------|
| `schema` | string | `snodo.models-benchmark.v1` |
| `ok` | bool | `true` when at least one run succeeded |
| `model` | string | fully qualified model selected for the benchmark |
| `snodo_version` | string | snodo version that produced the result |
| `prompt` | object | `{file, chars, sha256}` identity of the measured prompt |
| `attempted_runs` | int | requested and attempted calls |
| `succeeded_runs` | int | calls that returned a measurement |
| `samples` | array | one `{run, ok, ...metrics}` object per attempt; failures have `error` |
| `statistics` | object | successful-sample-only median and mean for first-token and throughput metrics |

Each successful sample preserves `output_tokens`, `prompt_tokens`,
`counts_basis`, `time_to_first_token`, `wall_seconds`, `decode_tok_per_sec`,
and `overall_tok_per_sec`. `--json` without `--benchmark` therefore has the
separate listing or stats schema above; it never mixes those payloads with a
benchmark result.

## Exit codes

`snodo validate` (and any command that returns a validation outcome) uses exit
codes that distinguish the validation outcomes, so a caller can branch without
parsing prose:

| Exit code | Outcome |
|-----------|---------|
| 0 | `pass` |
| 1 | `blocker` |
| 2 | `escalate` |
| 3 | `validator_error` |
| 4 | `internal_error` |

No command adds a sixth code. `environment_error` is an engine execution halt
(ADR 015), not a validation outcome, so it has no code here. `snodo survey` deliberately uses none of the
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

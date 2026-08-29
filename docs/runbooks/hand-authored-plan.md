# Runbook: authoring and running a plan by hand

`snodo plan create` scaffolds a plan but does not fill it in — the planner's
`decompose` step currently returns `{"waves": []}` unconditionally, so every
plan is created empty. This runbook shows the path that works end to end
today: create the plan, author the waves and task specs by hand on disk,
verify with `snodo plan validate`, and execute with `snodo run --plan <name>`.

The outputs below are from a real session (mock coder).

## Prerequisites

A snodo project (`snodo init` has been run) and a git repository with at
least one commit — execution creates a worktree per task from `HEAD`.

## 1. Create the plan

```console
$ snodo plan create "Ship the reporting endpoint" --name reporting
Plan created: reporting
  Intent: Ship the reporting endpoint
  Waves: 0
  Tasks: 0
```

Waves and tasks are always `0`: `create` writes only the scaffold. If you
omit `--name`, one is derived from the description
(lowercased, spaces to `_`, truncated to 40 characters).

## 2. The on-disk layout

```
.snodo/plans/reporting/
  plan.yml
  status.json
```

As created (`create` never writes waves — this is what you author into):

```yaml
# .snodo/plans/reporting/plan.yml
intent: Ship the reporting endpoint
name: reporting
waves: []
```

```json
// .snodo/plans/reporting/status.json
{
  "tasks": {}
}
```

Validating the scaffold fails, which is expected at this point:

```console
$ snodo plan validate reporting
Error: Plan verification failed for 'reporting':
  - No waves defined
```

Exit code is `1`.

## 3. Author the waves and task specs

A wave is `{id, depends_on[], tasks[]}`; ids must be integers, contiguous
from 1. A task id has the form `<wave>.<seq>_<name>` (e.g. `1.1_models`),
and its spec lives at `wave_<wave>/<task-id>_task.md`.

Edit `plan.yml` into a two-wave plan — wave 2 depends on wave 1:

```yaml
intent: Ship the reporting endpoint
name: reporting
waves:
- id: 1
  depends_on: []
  tasks:
  - 1.1_models
  - 1.2_endpoint
- id: 2
  depends_on:
  - 1
  tasks:
  - 2.1_smoke_test
```

Write one spec file per task. Leave one out for now to see the most common
authoring mistake:

```
.snodo/plans/reporting/
  plan.yml
  status.json
  wave_1/
    1.1_models_task.md        # 1.2_endpoint_task.md deliberately missing
  wave_2/
    # 2.1_smoke_test_task.md deliberately missing
```

```markdown
<!-- wave_1/1.1_models_task.md -->
# 1.1 models

Create `models.py` with a `Report` dataclass holding a `title` and a `rows` list.

## Acceptance criteria

- `models.py` exists and defines `Report`.
- `python -c "from models import Report"` succeeds.
```

Leave `status.json` as `{"tasks": {}}` — execution writes per-task entries
into it as it goes.

## 4. Validate — and the most common failure: a missing spec file

Every task listed in `plan.yml` must have its spec file on disk. If you
forgot one, `validate` names each:

```console
$ snodo plan validate reporting
Error: Plan verification failed for 'reporting':
  - Missing spec: 1.2_endpoint
  - Missing spec: 2.1_smoke_test
```

Exit code is `1`. The same check runs whenever a plan is loaded, so
`snodo plan status` fails the same way until the specs exist:

```console
$ snodo plan status reporting
Error: Plan violates well-formedness conditions:
  - Missing spec: 1.2_endpoint
  - Missing spec: 2.1_smoke_test
```

and `snodo run --plan reporting` refuses to start, printing the same errors.

Checklist `snodo plan validate` enforces (via `verify_plan`):

- `plan.yml` parses and has a non-empty `intent`; at least one wave.
- Wave ids are integers, contiguous from 1 (a `wave_3/` directory must not
  be expected where `plan.yml` lists no wave 3, and gaps like waves 1, 3
  are an error).
- `depends_on` references existing waves, with no dependency cycles.
- Every task listed in a wave has `wave_<id>/<task-id>_task.md` on disk.
- Every `status.json` entry matches a task in the waves (stale entries are
  an error); parent refs must resolve and must not cycle.
- A wave with no tasks is a warning, not an error.

Once every spec file exists, validation passes:

```console
$ snodo plan validate reporting
Plan 'reporting' validated successfully.
```

Add `--json` for the machine-readable form (`snodo.plan_validate.v1`):

```json
{
  "errors": [],
  "passed": true,
  "plan": "reporting",
  "schema": "snodo.plan_validate.v1",
  "warnings": []
}
```

## 5. Run the plan

```console
$ snodo run --plan reporting --mock
Plan: reporting
Intent: Ship the reporting endpoint

Wave 1:
  [1.1_models] executing...
  ...
  [1.2_endpoint] executing...
  ...
Wave 2:
  [2.1_smoke_test] executing...
  ...
```

Notes from the code path:

- Waves execute in id order; a wave's dependencies must be *completed
  waves* before its tasks run, otherwise it reports
  `Wave N: blocked (depends on: ...)`.
- Already-completed tasks are skipped (`[task] skipped (completed)`), so a
  run is resumable after a failure.
- `--wave N` executes only that wave; `--interactive` prompts
  `Execute <task-id>? [y/N]` before each task.
- The `quality` validator runs the project's test command after each task.
  If none is resolvable (no `tooling.test_command` in the protocol and no
  repo marker file it can auto-detect), the task halts with a
  `validator_error` blocker — set `tooling.test_command` in the protocol's
  quality validator config before running.
- If a task fails or blocks, the run stops there; later waves are not
  attempted. Exit code is `1`.

## 6. Check progress

```console
$ snodo plan status reporting
Plan: reporting
Intent: Ship the reporting endpoint

  Wave 1:
    [+] 1.1_models: completed
    [+] 1.2_endpoint: completed
  Wave 2 (depends on: 1):
    [+] 2.1_smoke_test: completed

Progress: 3/3 completed
```

```console
$ snodo plan list
Plans:
  reporting: Ship the reporting endpoint
    Waves: 2  Tasks: 3/3
```

Status markers: `[+]` completed, `[~]` in progress, `[!]` blocked,
`[ ]` pending. Until a run writes entries into `status.json`, tasks show as
pending and the `Progress:` line counts only status entries that exist — a
freshly hand-authored plan prints `Progress: 0/0 completed` even though its
waves list pending tasks.

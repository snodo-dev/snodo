# W2-03: Split cli/commands/run_cmd.py into three files

## Intent
run_cmd.py is 887 lines. Two clean extractions plus one helper relocation
reduce it to ~450 lines with no behavior change. Pure structural refactor.

## What to change

### cli/config.py — add _set_api_key_env
Move _set_api_key_env from run_cmd.py into cli/config.py alongside
ConfigManager. It is an infrastructure concern (model prefix → env var
mapping), not a CLI execution concern. Update all three callers:
- run_command (run_cmd.py)
- _submit_background_job (run_cmd.py)
- _run_plan (plan_run.py — see below)

### cli/commands/plan_run.py (new file)
Move these 7 functions:
- _run_plan
- _execute_waves
- _execute_wave_task
- _filter_waves
- _should_skip_task
- _task_completed
- _get_completed_waves
- _print_plan_progress

Imports needed: _execute_task from run_cmd, _set_api_key_env from
cli/config, load_protocol from cli/commands, ConfigManager from
cli/config, PlannerMCP from mcp/planner.

run_cmd.py imports _run_plan from plan_run.py at the call site (line 108).

### cli/commands/sandbox_run.py (new file)
Move these 4 functions:
- _run_in_sandbox
- _build_sandbox_command
- _build_sandbox_env
- _print_sandbox_result

Also move _submit_background_job here — it's a background job submission
concern, not core execution.

Imports needed: run_command from run_cmd, _set_api_key_env from
cli/config, DockerSandbox + SandboxConfig from snodo/sandbox,
ConfigManager from cli/config, JobManager from snodo/jobs.

run_cmd.py imports _run_in_sandbox from sandbox_run.py at the call
site (line 117).

### cli/commands/run_cmd.py (keep)
Everything else stays:
- run_command, _execute_task, _stream_execution, _build_graph
- _report_result, _print_stage, _render_halt_payload
- _serialize_policy_decision, _build_description
- _resolve_session, _setup_memory, _close_checkpointer
- _fetch_pr_context, _format_pr_comments
- _set_api_key_env REMOVED (moved to cli/config.py)

## Acceptance criteria
- run_cmd.py under 500 lines after extraction
- plan_run.py and sandbox_run.py each under 200 lines
- _set_api_key_env lives in cli/config.py, not run_cmd.py
- All existing behavior identical

## Testing
- Update test imports:
  tests/cli/test_run_cmd.py — _get_completed_waves and _should_skip_task
  import from snodo.cli.commands.plan_run
  tests/sandbox/test_sandbox.py — _run_in_sandbox import from
  snodo.cli.commands.sandbox_run
- Full test suite (1562 tests) passes clean
- If any test breaks, fix the refactor not the test

## Constraints
- Read run_cmd.py in full before touching anything
- One commit: all files + import updates together
- Do not change function signatures
- Do not change any logic

"""Plan command - Manage plans.

FILE: snodo/cli/commands/plan_cmd.py
"""

import logging
import re
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, List, Optional, Set

import typer

_logger = logging.getLogger(__name__)


def exec_option(name: str):
    """Return a shared ``snodo plan run`` execution option.

    Delegates to the single declaration in ``run_cmd`` so ``plan run`` exposes
    exactly the execution options ``snodo run`` does (Fixes #186).
    """
    from snodo.cli.commands.run_cmd import _execution_option
    return _execution_option(name)


# ---------------------------------------------------------------------------
# Self-registering Typer app (discovered by snodo/cli/main.py discovery loop)
# ---------------------------------------------------------------------------

COMMAND_NAME = "plan"

app = typer.Typer(invoke_without_command=True, help="Manage plans")


@app.callback()
def _plan_callback(ctx: typer.Context):
    """Manage plans."""
    if ctx.invoked_subcommand is None:
        print(ctx.get_help())


@app.command("list")
def plan_list():
    """List all plans."""
    args = SimpleNamespace(plan_action="list")
    return plan_command(args)


@app.command("status")
def plan_status(name: str = typer.Argument(..., help="Plan name")):
    """Show plan progress."""
    args = SimpleNamespace(plan_action="status", name=name)
    return plan_command(args)


@app.command("create")
def plan_create(
    description: str = typer.Argument(..., help="Intent/goal description for the plan"),
    plan_name: Optional[str] = typer.Option(
        None, "--name", "-n", help="Plan name (auto-generated if omitted)",
    ),
    protocol: str = typer.Option(
        ".snodo/protocol.yml", "--protocol", help="Path to protocol file",
    ),
    model: Optional[str] = typer.Option(
        None, "--model", "-m", help="Model to use",
    ),
    mock: bool = typer.Option(
        False, "--mock", help="Use mock coder instead of real LLM",
    ),
):
    """Create a new plan from an intent description."""
    args = SimpleNamespace(
        plan_action="create", description=description,
        plan_name=plan_name, protocol=protocol, model=model, mock=mock,
    )
    return plan_command(args)



@app.command("validate")
def plan_validate(
    name: str = typer.Argument(..., help="Plan name"),
    json_output: bool = typer.Option(
        False, "--json", help="Output validation result as JSON",
    ),
    protocol: str = typer.Option(
        ".snodo/protocol.yml", "--protocol", help="Path to protocol file",
    ),
):
    """Validate a plan's structure and spec files."""
    args = SimpleNamespace(
        plan_action="validate", name=name, json_output=json_output, protocol=protocol,
    )
    return plan_command(args)



@app.command("run")
def plan_run(
    name: str = typer.Argument(..., help="Plan name to execute"),
    wave: Optional[int] = exec_option("wave"),
    interactive: bool = exec_option("interactive"),
    protocol: str = typer.Option(
        ".snodo/protocol.yml", "--protocol", help="Path to protocol file",
    ),
    model: Optional[str] = exec_option("model"),
    coder: Optional[str] = exec_option("coder"),
    mode: Optional[str] = exec_option("mode"),
    verbose: bool = exec_option("verbose"),
    mock: bool = exec_option("mock"),
    retain_worktree: bool = exec_option("retain_worktree"),
    no_isolation: bool = exec_option("no_isolation"),
    fixture: Optional[str] = exec_option("fixture"),
):
    """Execute a plan's tasks through the protocol loop."""
    from snodo.cli.commands.run_cmd import RunArgs

    args = RunArgs(
        plan=name, wave=wave, interactive=interactive,
        protocol=protocol, model=model, coder=coder, mode=mode,
        verbose=verbose, mock=mock,
        retain_worktree=retain_worktree, no_isolation=no_isolation, fixture=fixture,
    )
    return _plan_run(args)


@app.command("add-task")
def plan_add_task(
    plan: str = typer.Argument(..., help="Plan name"),
    task_id: str = typer.Argument(..., help="Task ID, e.g. 1.1_models"),
    spec_file: str = typer.Option(..., "--spec-file", help="Path to the task spec file"),
    parent: Optional[str] = typer.Option(
        None, "--parent", help="Parent task reference (plan-scoped)",
    ),
    replace: bool = typer.Option(
        False, "--replace", help="Overwrite an existing task spec",
    ),
):
    """Add a task to a plan from a spec file."""
    args = SimpleNamespace(
        plan_action="add-task", plan=plan, task_id=task_id,
        spec_file=spec_file, parent=parent, replace=replace,
    )
    return plan_command(args)


@app.command("add-wave")
def plan_add_wave(
    plan: str = typer.Argument(..., help="Plan name"),
    id: str = typer.Argument(..., help="Wave id (integer)"),
    depends_on: Optional[str] = typer.Option(
        None, "--depends-on", help="Comma-separated wave ids this wave depends on",
    ),
):
    """Add a wave to a plan."""
    args = SimpleNamespace(
        plan_action="add-wave", plan=plan, id=id, depends_on=depends_on,
    )
    return plan_command(args)


@app.command("delete")
def plan_delete(
    name: str = typer.Argument(..., help="Plan name"),
    force: bool = typer.Option(
        False, "--force", help="Delete even if tasks are completed or in progress",
    ),
):
    """Delete a plan directory."""
    args = SimpleNamespace(plan_action="delete", name=name, force=force)
    return plan_command(args)


def plan_command(args) -> int:
    """Manage plans."""
    from snodo.mcp.planner import PlannerMCP
    from snodo.infrastructure.paths import require_project_root

    project_root = require_project_root()

    try:
        planner = PlannerMCP(project_root)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if args.plan_action == "list":
        return _plan_list(planner)
    elif args.plan_action == "status":
        return _plan_status(planner, args.name)
    elif args.plan_action == "create":
        return _plan_create(planner, args)
    elif args.plan_action == "validate":
        json_out = getattr(args, "json_output", False)
        protocol = getattr(args, "protocol", ".snodo/protocol.yml")
        completion_fn = getattr(args, "completion_fn", None)
        return _plan_validate(
            planner, args.name, json_output=json_out,
            protocol_path=protocol, completion_fn=completion_fn,
        )
    elif args.plan_action == "run":
        return _plan_run(args)
    elif args.plan_action == "add-task":
        return _plan_add_task(planner, args)
    elif args.plan_action == "add-wave":
        return _plan_add_wave(planner, args)
    elif args.plan_action == "delete":
        return _plan_delete(planner, args)
    else:
        print("Unknown plan action. Use: list, status, create, validate, run, add-task, add-wave, delete", file=sys.stderr)
        return 1


def _plan_list(planner) -> int:
    """List all plans."""
    plans = planner.list_plans()
    if not plans:
        print("No plans found.")
        return 0

    print("Plans:")
    for p in plans:
        counts = p.get("status_counts", {})
        done = counts.get("completed", 0)
        total = p["task_count"]
        progress = f"{done}/{total}" if total else "0/0"
        print(f"  {p['name']}: {p['intent']}")
        print(f"    Waves: {p['wave_count']}  Tasks: {progress}")
    return 0


def _plan_status(planner, name: str) -> int:
    """Show plan progress."""
    from snodo.mcp.planner import PlannerError

    try:
        plan_data = planner.get_plan(name)
        status_data = planner.get_status(name)
    except PlannerError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    tasks = status_data.get("tasks", {})
    print(f"Plan: {plan_data.get('name', name)}")
    print(f"Intent: {plan_data.get('intent', 'N/A')}")
    print()

    plan_job_id, task_jobs = _plan_job_index(planner.project_root, name)
    _print_plan_waves(plan_data.get("waves", []), tasks, task_jobs)
    _print_plan_summary(tasks)

    # Status is a snapshot; following the plan is one command that already
    # exists. Point at it rather than growing a second way to watch here.
    if plan_job_id:
        print()
        print(f"Follow this plan live: snodo logs {plan_job_id} --watch")
    return 0


def _plan_job_index(project_root, plan_name: str) -> tuple[Optional[str], dict]:
    """Map a plan to its run job and each task to the child job that ran it.

    The status view reads task state from disk, which never mentions that a
    job exists: a blocked row gave the reader no thread to pull. The engine
    already records it — the plan's run job carries ``plan``, each child
    carries ``parent_job`` — so read those records back and hand the ids to
    the row that needs them. Returns ``(latest_plan_job_id, {task_id: job_id})``
    or ``(None, {})`` when no job ran.
    """
    try:
        from snodo.jobs import JobManager
        jobs = JobManager(str(project_root)).list_jobs()
    except Exception as e:
        _logger.debug("Could not list jobs for plan %s: %s", plan_name, e)
        return None, {}

    plan_jobs = [j for j in jobs if j.get("plan") == plan_name]
    plan_job_id = plan_jobs[0]["id"] if plan_jobs else None

    task_jobs: dict[str, str] = {}
    for job in jobs:
        ref = job.get("task_ref")
        if ref and job.get("parent_job") == plan_job_id:
            task_jobs[ref] = job["id"]
    return plan_job_id, task_jobs


def _plan_create(planner, args) -> int:
    """Create a new plan from an intent description."""
    description = getattr(args, "description", "")
    plan_name = getattr(args, "plan_name", None)

    if not description:
        print("Error: plan description is required", file=sys.stderr)
        return 1

    from snodo.mcp.planner import PlannerError

    # Auto-generate name from description if not provided
    if not plan_name:
        plan_name = description.lower().replace(" ", "_")[:40].rstrip("_")

    try:
        plan_data = planner.decompose(description, plan_name)
    except PlannerError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    print(f"Plan created: {plan_name}")
    print(f"  Intent: {description}")
    waves = plan_data.get("waves", [])
    print(f"  Waves: {len(waves)}")
    total_tasks = sum(len(w.get("tasks", [])) for w in waves)
    print(f"  Tasks: {total_tasks}")
    return 0


def _print_plan_waves(waves: list, tasks: dict,
                      task_jobs: Optional[dict] = None) -> None:
    """Print wave and task details.

    A row that needs a look (blocked, errored, unmerged, in progress) carries
    the real command that reaches its logs, so the id a reader needs is beside
    the row it describes. Healthy and never-run rows stay free of that noise.
    """
    _STATUS_MARKERS = {"completed": "+", "in_progress": "~",
                       "blocked": "!", "errored": "?",
                       "unmerged": "u", "pending": " "}
    task_jobs = task_jobs or {}
    for wave in waves:
        wave_id = wave.get("id")
        deps = wave.get("depends_on", [])
        dep_str = f" (depends on: {', '.join(str(d) for d in deps)})" if deps else ""
        print(f"  Wave {wave_id}{dep_str}:")
        for task_id in wave.get("tasks", []):
            raw = tasks.get(task_id, "pending")
            state = raw["status"] if isinstance(raw, dict) else raw
            marker = _STATUS_MARKERS.get(state, "?")
            job_id = task_jobs.get(task_id)
            if job_id and state not in ("completed", "pending"):
                print(f"    [{marker}] {task_id}: {state}  ->  snodo logs {job_id}")
            else:
                print(f"    [{marker}] {task_id}: {state}")
    print()


def _print_plan_summary(tasks: dict) -> None:
    """Print plan progress summary."""
    total = len(tasks)
    done = sum(1 for s in tasks.values() if (s["status"] if isinstance(s, dict) else s) == "completed")
    blocked = sum(1 for s in tasks.values() if (s["status"] if isinstance(s, dict) else s) == "blocked")
    errored = sum(1 for s in tasks.values() if (s["status"] if isinstance(s, dict) else s) == "errored")
    unmerged = sum(1 for s in tasks.values() if (s["status"] if isinstance(s, dict) else s) == "unmerged")
    print(f"Progress: {done}/{total} completed", end="")
    if unmerged:
        print(f", {unmerged} unmerged", end="")
    if blocked:
        print(f", {blocked} blocked", end="")
    if errored:
        print(f", {errored} errored", end="")
    print()


def _is_spec_only_validator(v: Any) -> bool:
    """Return True if *v* evaluates the spec prose alone without repository access.

    A validator is eligible iff:
    1. It explicitly declares that it judges the specification (judges_spec: true,
       established in ADR 023).
    2. It declares no repository read tools (tools list is empty).
    3. Its evaluation phase is pre_execute.
    4. It declares no tooling commands (e.g. test_command).

    Requiring judges_spec and no tools prevents running repo-dependent judges
    against a repo they cannot see (Fixes #350).
    """
    return bool(
        getattr(v, "judges_spec", False)
        and not getattr(v, "tools", None)
        and getattr(v, "evaluation_phase", "pre_execute") == "pre_execute"
        and not getattr(v, "tooling", {}).get("test_command")
    )


def _judge_plan_specs(
    planner: Any,
    plan_dir: Path,
    protocol_path: Optional[str] = ".snodo/protocol.yml",
    completion_fn: Any = None,
) -> List[str]:
    """Run eligible spec-only validators against task specs in *plan_dir*.

    A verdict reached at plan-validate time is advice, not a gate: it produces
    advisory warnings before dispatching without blocking validation or dispatching.
    No repo-reading validators run, and plan-time verdicts are never cached for
    loop reuse (Fixes #350).
    """
    if not protocol_path:
        return []

    p_path = Path(protocol_path)
    if not p_path.is_absolute():
        p_path = Path(planner.project_root) / p_path
    if not p_path.is_file():
        return []

    try:
        from snodo.protocols import load_protocol
        protocol = load_protocol(p_path)
    except Exception as e:
        _logger.debug("Could not load protocol from %s for plan validation: %s", p_path, e)
        return []

    if protocol is None:
        return []

    mode_id = protocol.initial_mode
    mode = protocol.get_mode(mode_id)
    if mode:
        candidate_validators = [protocol.get_validator(vid) for vid in mode.validators]
        candidate_validators = [v for v in candidate_validators if v is not None]
    else:
        candidate_validators = protocol.validators

    eligible_validators = []
    seen: Set[str] = set()
    for v in candidate_validators:
        if v.validator_id not in seen and _is_spec_only_validator(v):
            seen.add(v.validator_id)
            eligible_validators.append(v)

    if not eligible_validators:
        return []

    default_model = None
    if completion_fn is None:
        try:
            from snodo.validators.runner import resolve_validator_completion
            completion_fn, default_model, _ = resolve_validator_completion()
        except Exception as e:
            _logger.debug("Could not resolve validator completion for plan validate: %s", e)
            return []
    else:
        from snodo.infrastructure.config import DEFAULT_MODEL
        default_model = DEFAULT_MODEL

    plan_file = plan_dir / "plan.yml"
    if not plan_file.is_file():
        return []

    import yaml
    try:
        with open(plan_file) as f:
            plan_data = yaml.safe_load(f) or {}
    except Exception:
        return []

    waves = plan_data.get("waves", [])
    if not isinstance(waves, list):
        return []

    from snodo.compiler.models import Severity
    from snodo.core.interfaces import Task, ValidatorResult
    from snodo.validators.context import ValidatorContext
    from snodo.validators.llm_validator import LLMValidator
    from snodo.validators.registry import _default_registry as reg
    from snodo.validators.runner import enrich_result_with_criteria

    warnings: List[str] = []
    for wave in waves:
        if not isinstance(wave, dict):
            continue
        wave_id = wave.get("id")
        tasks = wave.get("tasks", [])
        if not isinstance(tasks, list):
            continue
        wave_dir = plan_dir / f"wave_{wave_id}"
        for task_id in tasks:
            spec_file = wave_dir / f"{task_id}_task.md"
            if not spec_file.is_file():
                continue
            try:
                spec_text = spec_file.read_text(encoding="utf-8")
            except Exception as e:
                _logger.debug("Could not read spec file %s: %s", spec_file, e)
                continue

            task = Task(id=task_id, spec=spec_text)

            for v in eligible_validators:
                effective_model = v.model or default_model
                cls = reg.lookup(v.validator_type) or LLMValidator
                try:
                    instance = cls(validator_spec=v, completion_fn=completion_fn, model=effective_model)
                    ctx = ValidatorContext(
                        task=task,
                        current_mode=mode,
                        protocol=protocol,
                        completion_fn=completion_fn,
                        model=effective_model,
                        phase="pre_execute",
                        task_id=task.id,
                        verdict_cache=None,  # Do not cache plan-time verdicts for loop reuse
                    )
                    res = instance.evaluate(ctx)
                    res = enrich_result_with_criteria(res, v.criteria)
                    if v.severity_cap is not None and not getattr(res, "error", False) and res.severity is not None:
                        try:
                            if Severity(res.severity) > v.severity_cap:
                                res = ValidatorResult(
                                    validator_id=res.validator_id,
                                    severity=v.severity_cap.value,
                                    justification=res.justification,
                                    cited_criteria=res.cited_criteria,
                                    severity_original=res.severity,
                                )
                        except (ValueError, KeyError):
                            pass
                    if not getattr(res, "error", False) and res.severity in ("warn", "blocker"):
                        warnings.append(
                            f"[{task_id}] {res.validator_id} [{res.severity}]: {res.justification}"
                        )
                except Exception as e:
                    _logger.debug(
                        "Plan validate: validator %s failed on task %s: %s",
                        v.validator_id, task_id, e,
                    )
    return warnings


def _plan_validate(
    planner,
    name: str,
    json_output: bool = False,
    protocol_path: Optional[str] = ".snodo/protocol.yml",
    completion_fn: Any = None,
) -> int:
    """Validate a plan's structure and spec files."""
    from snodo.compiler.verifier import verify_plan_dir

    plan_dir = planner.plans_dir / name
    result = verify_plan_dir(plan_dir, workspace_root=planner.project_root)

    if result.passed:
        spec_warnings = _judge_plan_specs(
            planner,
            plan_dir,
            protocol_path=protocol_path,
            completion_fn=completion_fn,
        )
        result.warnings.extend(spec_warnings)

    if json_output:
        from snodo.cli.json_output import emit_json, schema_name
        payload = {
            "schema": schema_name("plan_validate"),
            "plan": name,
            "passed": result.passed,
            "errors": result.errors,
            "warnings": result.warnings,
        }
        return emit_json(payload, exit_code=0 if result.passed else 1)

    if result.warnings:
        print("Warnings:", file=sys.stderr)
        for w in result.warnings:
            print(f"  - {w}", file=sys.stderr)

    if not result.passed:
        print(f"Error: Plan verification failed for '{name}':", file=sys.stderr)
        for err in result.errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print(f"Plan '{name}' validated successfully.")
    return 0


def _plan_run(args) -> int:
    """Execute a plan's tasks through the protocol loop.

    Builds the same SimpleNamespace ``snodo run --plan`` builds and delegates
    to ``snodo.cli.commands.plan_run._run_plan`` (Fixes #130). Imported inside
    the function so ``snodo run --plan`` keeps working unchanged.
    """
    from snodo.cli.commands.plan_run import _run_plan

    return _run_plan(args)


_TASK_ID_RE = re.compile(r"^\d+\.\d+_[A-Za-z0-9_-]+$")


def _plan_add_task(planner, args) -> int:
    """Add a task to a plan from a spec file (Fixes #130)."""
    from snodo.mcp.planner import PlannerError

    plan = getattr(args, "plan", "")
    task_id = getattr(args, "task_id", "")
    spec_file = getattr(args, "spec_file", "")
    parent = getattr(args, "parent", None)
    replace = bool(getattr(args, "replace", False))

    if not _TASK_ID_RE.match(task_id):
        print(
            f"Error: invalid task id '{task_id}'. Expected <wave>.<seq>_<name>, "
            "e.g. 1.1_models",
            file=sys.stderr,
        )
        return 1

    spec_path = Path(spec_file)
    if not spec_path.exists():
        print(f"Error: spec file not found: {spec_file}", file=sys.stderr)
        return 1
    spec = spec_path.read_text()

    try:
        rel = planner.generate_spec(
            plan, task_id, spec,
            parent_task_ref=parent, replace=replace,
        )
    except PlannerError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    print(f"Task {task_id} added to plan {plan} (spec: {rel})")

    # The plan must remain verifiable — never silently produce a broken plan.
    from snodo.compiler.verifier import verify_plan_dir
    result = verify_plan_dir(planner.plans_dir / plan, workspace_root=planner.project_root)
    if not result.passed:
        print("Error: plan is now invalid:", file=sys.stderr)
        for err in result.errors:
            print(f"  - {err}", file=sys.stderr)
        return 1
    return 0


def _plan_add_wave(planner, args) -> int:
    """Add a wave to a plan (Fixes #130)."""
    plan = getattr(args, "plan", "")
    wave_id = getattr(args, "id", "")
    depends_on = getattr(args, "depends_on", None)

    if not str(wave_id).isdigit():
        print(
            f"Error: wave id '{wave_id}' is not an integer. Wave ids are integers.",
            file=sys.stderr,
        )
        return 1
    wave_num = int(wave_id)

    deps: list = []
    if depends_on:
        for part in str(depends_on).split(","):
            part = part.strip()
            if not part.isdigit():
                print(
                    f"Error: dependency '{part}' is not an integer wave id.",
                    file=sys.stderr,
                )
                return 1
            deps.append(int(part))

    plan_dir = planner.plans_dir / plan
    if not plan_dir.exists():
        print(f"Error: plan not found: {plan}", file=sys.stderr)
        return 1

    plan_file = plan_dir / "plan.yml"
    import yaml
    with open(plan_file) as f:
        plan_data = yaml.safe_load(f) or {}
    waves = plan_data.setdefault("waves", [])

    existing = {w.get("id"): w for w in waves}

    # `plan create` scaffolds wave 1, so `add-wave 1` is the natural next
    # command — not a failure. Adding a wave that already exists is idempotent:
    # it succeeds without duplicating, and updates dependencies only when asked.
    if wave_num in existing:
        for dep in deps:
            if dep == wave_num or dep not in existing:
                print(
                    f"Error: wave {wave_num} depends on wave {dep}, which does not exist "
                    f"in plan {plan}",
                    file=sys.stderr,
                )
                return 1
        if depends_on:
            existing[wave_num]["depends_on"] = deps
            with open(plan_file, "w") as f:
                yaml.dump(plan_data, f, default_flow_style=False)
            print(f"Wave {wave_num} already exists in plan {plan}; dependencies updated")
        else:
            print(f"Wave {wave_num} already exists in plan {plan} (nothing to add)")
        return 0

    for dep in deps:
        if dep == wave_num or dep not in existing:
            print(
                f"Error: wave {wave_num} depends on wave {dep}, which does not exist "
                f"in plan {plan}",
                file=sys.stderr,
            )
            return 1

    waves.append({"id": wave_num, "depends_on": deps, "tasks": []})
    waves.sort(key=lambda w: w["id"])
    with open(plan_file, "w") as f:
        yaml.dump(plan_data, f, default_flow_style=False)

    print(f"Wave {wave_num} added to plan {plan}")

    # The plan must remain verifiable — never silently produce a broken plan.
    from snodo.compiler.verifier import verify_plan_dir
    result = verify_plan_dir(plan_dir, workspace_root=planner.project_root)
    if not result.passed:
        print("Error: plan is now invalid:", file=sys.stderr)
        for err in result.errors:
            print(f"  - {err}", file=sys.stderr)
        return 1
    return 0


def _plan_delete(planner, args) -> int:
    """Delete a plan directory (Fixes #130)."""
    name = getattr(args, "name", "")
    force = bool(getattr(args, "force", False))

    plan_dir = planner.plans_dir / name
    if not plan_dir.exists():
        print(f"Error: plan not found: {name}", file=sys.stderr)
        return 1

    if not force:
        try:
            status_data = planner.get_status(name)
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
        tasks = status_data.get("tasks", {})
        active = [
            tid for tid, entry in tasks.items()
            if (entry.get("status") if isinstance(entry, dict) else entry)
            in ("completed", "in_progress")
        ]
        if active:
            print(
                "Error: refusing to delete plan with active tasks: "
                + ", ".join(sorted(active))
                + ". Use --force to delete anyway.",
                file=sys.stderr,
            )
            return 1

    shutil.rmtree(plan_dir, ignore_errors=True)
    print(f"Plan '{name}' deleted.")
    return 0

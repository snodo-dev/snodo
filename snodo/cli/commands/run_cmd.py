"""Run command - Execute tasks through protocol loop.

FILE: snodo/cli/commands/run_cmd.py
"""

import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import typer

from snodo.compiler.models import Protocol
from snodo.core.interfaces import Task
from snodo.config import ConfigManager, provider_env
from snodo.cli.commands import load_protocol


# === Shared execution options (single declaration) ===
#
# `snodo run` and `snodo plan run` drive the same execution — plan run walks the
# same planner/runner as `snodo run --plan` — so they must expose the same
# options. Options are declared HERE, once, and both commands bind the shared
# instance, so the flag names, help text and types cannot drift (Fixes #186).
# This is the divergence behind the missing `--coder` on `snodo plan run`, and
# it has now cost two defects: an option added to one command silently did not
# exist on the other.
#
# The shared subset is exactly the RunArgs fields that are settable from the
# CLI. A single declaration means the option surface and the args object are
# derived from one place and cannot disagree.


def _execution_option(name: str) -> typer.Option:
    """Return the shared ``snodo run`` / ``snodo plan run`` option by name.

    The dict is built once at import and every command parameter binds the
    shared instance, so a change here is visible to both commands and to the
    surface test (``tests/cli/test_surface_parity.py``).
    """
    return _EXECUTION_OPTIONS[name]


def _build_execution_options() -> dict:
    """Declare the shared execution options for ``run`` and ``plan run``.

    Kept as a function so the declaration reads as one block; evaluated once at
    import into ``_EXECUTION_OPTIONS``.
    """
    return {
        "model": typer.Option(None, "--model", "-m",
                              help="Model to use (e.g., claude-sonnet-4-20250514, gpt-4)"),
        "coder": typer.Option(None, "--coder",
                              help="Coder backend name (e.g., litellm, opencode-cli, mock)"),
        "mode": typer.Option(None, "--mode", help="Execution mode override"),
        "verbose": typer.Option(False, "--verbose", help="Show detailed output"),
        "mock": typer.Option(False, "--mock", help="Use mock coder instead of real LLM"),
        "wave": typer.Option(None, "--wave", "-w",
                             help="Execute only a specific wave (requires --plan)"),
        "interactive": typer.Option(False, "--interactive", "-i",
                                    help="Confirm each task before execution"),
        "no_isolation": typer.Option(False, "--no-isolation",
                                     help="Run without worktree isolation even when one cannot be created. "
                                          "Never set automatically: losing isolation is a warning, not an error."),
        "retain_worktree": typer.Option(False, "--retain-worktree",
                                        help="Keep the task worktree regardless of outcome"),
    }


_EXECUTION_OPTIONS = _build_execution_options()


@dataclass(frozen=True)
class RunArgs:
    """CLI execution arguments for snodo run / snodo plan run."""

    description: Optional[str] = None
    protocol: str = ".snodo/protocol.yml"
    model: Optional[str] = None
    coder: Optional[str] = None
    mode: Optional[str] = None
    verbose: bool = False
    mock: bool = False
    plan: Optional[str] = None
    wave: Optional[int] = None
    interactive: bool = False
    from_pr: Optional[int] = None
    background: bool = False
    sandbox: str = "local"
    resume: Optional[str] = None
    retry: Optional[str] = None
    retain_worktree: bool = False
    no_isolation: bool = False
    audit_log: Optional[Any] = None
    session_manager: Optional[Any] = None


def register(app: typer.Typer) -> None:
    """Register top-level CLI commands onto app (called by discovery loop)."""

    @app.command()
    def run(
        description: Optional[str] = typer.Argument(
            None, help="Task description (required unless --plan is used)",
        ),
        protocol: str = typer.Option(
            ".snodo/protocol.yml", "--protocol", help="Path to protocol file",
        ),
        model: Optional[str] = _execution_option("model"),
        coder: Optional[str] = _execution_option("coder"),
        mode: Optional[str] = _execution_option("mode"),
        verbose: bool = _execution_option("verbose"),
        mock: bool = _execution_option("mock"),
        plan: Optional[str] = typer.Option(
            None, "--plan", "-p", help="Execute a plan by name",
        ),
        wave: Optional[int] = _execution_option("wave"),
        interactive: bool = _execution_option("interactive"),
        from_pr: Optional[int] = typer.Option(
            None, "--from-pr", help="Fetch PR comments as task context",
        ),
        background: bool = typer.Option(
            False, "--background", "-b", help="Run task in background",
        ),
        sandbox: str = typer.Option(
            "local", "--sandbox", help="Sandbox type: local or docker",
        ),
        resume: Optional[str] = typer.Option(
            None, "--resume", help="Resume execution from session ID",
        ),
        retry: Optional[str] = typer.Option(
            None, "--retry", help="Retry a failed task by ID (requires P0 branch isolation)",
        ),
        retain_worktree: bool = _execution_option("retain_worktree"),
        no_isolation: bool = _execution_option("no_isolation"),
    ):
        """Execute a task through the protocol."""
        args = RunArgs(
            description=description, protocol=protocol, model=model, coder=coder, mode=mode,
            verbose=verbose, mock=mock, plan=plan, wave=wave,
            interactive=interactive, from_pr=from_pr, background=background,
            sandbox=sandbox, resume=resume, retry=retry,
            retain_worktree=retain_worktree, no_isolation=no_isolation,
        )
        return run_command(args)


_logger = logging.getLogger(__name__)


def _format_pr_comments(data: dict) -> list:
    """Format PR comments and reviews into text lines.

    Args:
        data: Parsed PR JSON data with comments and reviews

    Returns:
        List of formatted comment strings
    """
    parts = []
    title = data.get("title", "")
    if title:
        parts.append(f"PR Title: {title}")

    comments = data.get("comments", [])
    reviews = data.get("reviews", [])

    if not comments and not reviews:
        return parts

    parts.append("\nReview Comments:")
    for c in comments:
        author = c.get("author", {}).get("login", "unknown")
        body = c.get("body", "").strip()
        if body:
            parts.append(f"  @{author}: {body}")
    for r in reviews:
        author = r.get("author", {}).get("login", "unknown")
        body = r.get("body", "").strip()
        state = r.get("state", "")
        if body:
            parts.append(f"  @{author} [{state}]: {body}")
    return parts


def _fetch_pr_context(pr_number: int, project_root: str) -> str:
    """Fetch PR comments and diff as context string.

    Args:
        pr_number: PR number to fetch context from
        project_root: Project root directory

    Returns:
        Formatted context string with PR title, comments, reviews, and diff
    """
    from snodo.mcp.pr import PrMCP, PrError
    from snodo.providers.registry import detect_provider

    provider = None
    try:
        provider = detect_provider(project_root)
    except Exception as e:
        _logger.debug("PR context: provider detection failed: %s", e)
    pr = PrMCP(project_root, provider=provider)
    parts = [f"--- PR #{pr_number} Review Context ---"]

    try:
        comments_json = pr.read_pr_comments(pr_number)
        parts.extend(_format_pr_comments(json.loads(comments_json)))
    except PrError as e:
        parts.append(f"(Could not fetch PR comments: {e})")

    try:
        diff = pr.read_pr_diff(pr_number)
        if diff.strip():
            parts.append(f"\nDiff:\n{diff}")
    except PrError:
        parts.append("(Could not fetch PR diff)")

    parts.append("--- End PR Context ---")
    return "\n".join(parts)


def _print_missing_template_validators(protocol) -> None:
    """Tell the operator when their project's validator set is out of date.

    Adding a validator to a shipped template does NOT add it to a project
    whose .snodo/protocol.yml predates the change.  A project running an
    out-of-date validator set should be able to find out — otherwise a
    validator that was added to close a pipeline hole silently does nothing
    for that project (Fixes #59).
    """
    try:
        from snodo.protocols import missing_template_validators
        missing = missing_template_validators(protocol)
    except Exception:
        return
    if not missing:
        return
    print(
        f"  ⚠ Validator set is out of date: this project's protocol predates "
        f"{', '.join(missing)}. "
        f"Regenerate .snodo/protocol.yml (snodo init --force) or add them "
        f"manually to run the current validator set."
    )


def _preflight_provider_credential(model: str) -> Optional[str]:
    """Return an error message when *model*'s provider has no credential, else None.

    Reuses ConfigManager's resolution so the preflight cannot disagree with the
    thing that actually loads the key: the provider is resolved via
    ``_provider_for_model`` and the credential via ``get_key_for_model`` (config)
    or the provider's ``api_key_env`` (environment). A provider that declares no
    credential env var (e.g. a local endpoint) is not preflighted.
    """
    mgr = ConfigManager()
    provider = ConfigManager._provider_for_model(model)
    if provider is None:
        return None
    pc = mgr.get_providers().get(provider)
    env_var = pc.api_key_env if pc else ""
    if mgr.get_key_for_model(model):
        return None
    if env_var and os.environ.get(env_var):
        return None
    if not env_var:
        return None
    return (
        f"Error: No credential for provider '{provider}' (model {model}). "
        f"Set {env_var} or run: snodo config add {provider} <key>"
    )


def run_command(args) -> int:
    """Execute task through protocol loop - REAL EXECUTION."""
    from snodo.infrastructure.audit import get_audit_log
    from snodo.infrastructure.session import SessionManager
    from snodo.infrastructure.paths import require_project_root
    from snodo.cli.commands.plan_run import _run_plan
    from snodo.cli.commands.sandbox_run import _run_in_sandbox, _submit_background_job

    project_root = require_project_root()
    from snodo.project import get_project_id
    project_id, _ = get_project_id(project_root)
    audit_log = get_audit_log(project_id=project_id)
    session_manager = SessionManager(audit_log=audit_log)
    import dataclasses
    if dataclasses.is_dataclass(args):
        args = dataclasses.replace(args, audit_log=audit_log, session_manager=session_manager)
    else:
        args.audit_log = audit_log
        args.session_manager = session_manager

    if getattr(args, "background", False):
        return _submit_background_job(args)

    if getattr(args, "plan", None):
        return _run_plan(args)

    retry_task_id = getattr(args, "retry", None)
    if retry_task_id:
        return _retry_task(args, retry_task_id, project_root, session_manager)

    if args.description is None:
        print("Error: task description required (or use --plan <name>)", file=sys.stderr)
        return 1

    # Route through docker sandbox if requested
    sandbox_type = getattr(args, "sandbox", "local")
    if sandbox_type == "docker":
        return _run_in_sandbox(args)

    mgr = ConfigManager()
    model = args.model or mgr.get_model()

    # Preflight the provider credential before any session, worktree or graph
    # work (Fixes #137): a fresh install must fail immediately with one line,
    # not after the run has already created a session, a worktree and a graph.
    # Skipped when the coder is mocked — no LLM will be called.
    if not getattr(args, "mock", False):
        error = _preflight_provider_credential(model)
        if error:
            print(error, file=sys.stderr)
            return 1

    protocol_path = Path(args.protocol)
    if not protocol_path.is_absolute():
        protocol_path = Path(project_root) / args.protocol
    protocol = load_protocol(protocol_path)
    if not protocol:
        return 1

    print(f"✓ Loaded protocol: {protocol.name}")
    print(f"  Modes: {', '.join(m.mode_id for m in protocol.modes)}")
    print(f"  Validators: {', '.join(v.validator_id for v in protocol.validators)}")
    print(f"  Policy: {protocol.disagreement_policy.value}")
    print(f"  Model: {model}")
    _print_missing_template_validators(protocol)
    print()

    description = _build_description(args)

    from snodo.paths import derive_task_id

    task = Task(
        id=derive_task_id(description),
        spec=description
    )

    with provider_env(model) as mgr:
        return _execute_task(args, protocol, task, model)


def _build_description(args) -> str:
    """Build task description, optionally prepending PR context.

    Args:
        args: Parsed CLI arguments with description and optional from_pr

    Returns:
        Final task description string
    """
    description = args.description
    from_pr = getattr(args, "from_pr", None)
    if from_pr is not None:
        from snodo.infrastructure.paths import require_project_root
        project_root = require_project_root()
        print(f"Fetching PR #{from_pr} context...")
        pr_context = _fetch_pr_context(from_pr, project_root)
        description = f"{pr_context}\n\n{description}"
        print("  PR context prepended to task spec")
        print()
    return description


def _failure_from_halt_record(session, task_id: str) -> Optional[dict]:
    """Synthesise retry failure context from a persisted halt record (Fixes #121).

    Tasks that halted before commit eab9696 (or on any path that wrote a halt
    but no task_failure entry) still carry a structured halt payload at
    ``decisions["halt"][task_id]`` with task_spec, reason and validator
    results — enough to reconstruct the failure context ``_retry_task`` needs.
    Returns None unless the halt record's task_id matches and it is a blocked
    halt; ``task_failure`` remains the preferred source when present.
    """
    from snodo.engine.state import _task_branch_name

    halt = session.checkpoint.decisions.get("halt", {})
    if not isinstance(halt, dict):
        return None
    record = halt.get(task_id)
    if not isinstance(record, dict) or record.get("task_id") != task_id:
        return None
    if record.get("status") != "blocked":
        return None

    spec = record.get("task_spec", "")
    validator_results = record.get("validator_results")
    failed_validators = [
        {
            "validator_id": v.get("validator_id", "unknown"),
            "severity": v.get("severity", "blocker"),
            "justification": v.get("justification", ""),
        }
        for v in (validator_results or [])
        if isinstance(v, dict) and v.get("severity") in ("blocker", "warn")
    ]
    if not failed_validators and record.get("reason"):
        failed_validators = [
            {
                "validator_id": record.get("halt_type") or "execution_error",
                "severity": "blocker",
                "justification": record["reason"],
            }
        ]

    return {
        "spec": spec,
        "original_spec": spec,
        "branch": _task_branch_name(task_id, spec),
        "attempt": 1,
        "phase": record.get("phase", "post_execute"),
        "failed_validators": failed_validators,
        "files_changed": [],
    }


def _retry_task(args, task_id: str, project_root: str, session_manager) -> int:
    """Retry a failed task on its existing branch with failure context."""
    from snodo.infrastructure.state import read_state

    state = read_state(project_root)
    mode = state.current_mode or "producer"

    session = session_manager.get_active_session(mode, project_root)
    if session is None:
        print(f"Error: No active session for mode={mode}", file=sys.stderr)
        return 1

    task_failure = session.checkpoint.decisions.get("task_failure", {})
    if not isinstance(task_failure, dict):
        task_failure = {}

    failure = task_failure.get(task_id)
    if not isinstance(failure, dict):
        failure = _failure_from_halt_record(session, task_id)
    if failure is None:
        print(f"No failure context for {task_id}. Cannot retry.", file=sys.stderr)
        return 1

    attempt = failure.get("attempt", 0)

    protocol_path = Path(args.protocol)
    if not protocol_path.is_absolute():
        protocol_path = Path(project_root) / args.protocol
    protocol = load_protocol(protocol_path)
    if not protocol:
        return 1

    max_retries = getattr(protocol.execution, "max_retries", 3)
    if attempt >= max_retries:
        print(f"Task {task_id} has failed {max_retries} times.")
        print(f"  Review branch {failure.get('branch', 'unknown')} and either:")
        print(f"  - snodo run --retry {task_id} \"revised spec\" (override spec)")
        print(f"  - snodo task abandon {task_id} (delete branch)")
        return 1

    # Clear stale pending_decisions from previous attempt
    pending = session.checkpoint.decisions.get("pending_decisions", {})
    if isinstance(pending, dict):
        pending.pop(task_id, None)
        session_manager.update_decision(
            session.session_id, "pending_decisions", pending,
        )

    # Build augmented prompt
    original_spec = failure.get("original_spec") or failure.get("spec", "")
    revised_spec = args.description
    authoritative_spec = revised_spec or original_spec

    failed_validators = failure.get("failed_validators", [])
    validator_details = "\n".join(
        f"  {v['validator_id']}: {v['justification']}"
        for v in failed_validators
    )
    raw_files = failure.get("files_changed", [])
    if isinstance(raw_files, list):
        files_changed = ", ".join(f for f in raw_files if isinstance(f, str) and f.strip())
    else:
        files_changed = ""

    phase = failure.get("phase", "post_execute")
    if phase == "pre_execute":
        phase_label = "pre-validation"
    elif phase == "execute":
        phase_label = "at execute"
    else:
        phase_label = "post-validation"

    prompt_parts = []
    if revised_spec:
        prompt_parts.append(f"Original spec: {original_spec}")
        prompt_parts.append(f"Revised spec (replaces original): {revised_spec}")
    else:
        prompt_parts.append(f"Original spec: {original_spec}")

    if validator_details:
        prompt_parts.append(f"Previous attempt {attempt} failed {phase_label}:\n{validator_details}")
    else:
        prompt_parts.append(f"Previous attempt {attempt} failed {phase_label}.")

    if files_changed:
        prompt_parts.append(f"Files changed in previous attempt: {files_changed}")

    prompt_parts.append("Fix the issues above.")

    augmented = "\n\n".join(prompt_parts)

    mgr = ConfigManager()
    model = args.model or mgr.get_model()

    task = Task(id=task_id, spec=augmented, root_spec=authoritative_spec)
    print(f"Retrying task {task_id} (attempt {attempt + 1}/{max_retries})")
    print()

    with provider_env(model) as mgr:
        return _execute_task(args, protocol, task, model)


def _execute_task(args, protocol: Protocol, task: Task, model: str) -> int:
    """Execute a single task through the protocol graph.

    This is the session lifecycle wiring point:
    - Auto-start or resume session
    - Pass session_manager into build_protocol_graph
    - Save checkpoint on exit
    """
    print(f"Task: {task.spec}")
    print(f"Task ID: {task.id}")
    print(f"  Inspect: snodo task show {task.id}")
    print()

    from snodo.infrastructure.paths import require_project_root
    project_root = require_project_root()
    old_project_root = os.environ.get("SNODO_PROJECT_ROOT")
    os.environ["SNODO_PROJECT_ROOT"] = str(project_root)
    _record_task_start(project_root, task.id, task.spec)
    audit_log = getattr(args, "audit_log", None)
    session_manager = getattr(args, "session_manager", None)

    # Session lifecycle: start or resume. Resolve the active mode once here so
    # the session and the execution can never disagree about which mode is active.
    session, mode = _resolve_session(args, session_manager, protocol, project_root)
    session_id = session.session_id if session else None

    if session is not None and session.mode != mode:
        print(f"Error: Session mode '{session.mode}' does not match active mode '{mode}'",
              file=sys.stderr)
        raise SystemExit(1)

    if session_id and session_manager:
        session_manager.set_current_task(session_id, task.id)

    # Set up agent memory
    memory_mgr, checkpointer, thread_config = _setup_memory(project_root, protocol, mode)

    job_id = os.environ.get("SNODO_JOB_ID") or None

    # Background job only: prefer stored task_id over hash-computed one.
    # Guard against leaked env var (test isolation gap) — skip silently when
    # the job dir does not exist (e.g. inline / non-background runs).
    if job_id:
        from snodo.jobs import JobManager
        mgr = JobManager(project_root)
        job_dir = mgr.jobs_dir / job_id
        if job_dir.is_dir():
            stored = mgr._load_task(job_dir)
            stored_task_id = stored.get("task_id") or stored.get("retry_task_id")
            if stored_task_id and stored_task_id != task.id:
                task = Task(id=stored_task_id, spec=task.spec)

            # Persist task_id into task.json for same-task retry lookup
            task_json_path = job_dir / "task.json"
            if task_json_path.exists():
                try:
                    data = json.loads(task_json_path.read_text())
                    data["task_id"] = task.id
                    task_json_path.write_text(json.dumps(data, indent=2) + "\n")
                except Exception as e:
                    _logger.warning(
                        "Could not persist task_id into %s for retry lookup: %s",
                        task_json_path, e,
                    )

    # Set up git worktree — shared helper used by BOTH CLI inline and background
    from snodo.infrastructure.worktree import (
        WorktreeIsolationError, setup_for_task, remove_worktree, delete_task_branch,
    )
    existing_wt = os.environ.get("SNODO_WORKTREE_PATH")
    no_isolation = bool(getattr(args, "no_isolation", False))

    try:
        worktree_path_val = setup_for_task(
            project_root, task.id, task.spec, existing_worktree_path=existing_wt
        )
        worktree_failure = None
    except Exception as exc:  # noqa: BLE001 — isolation loss must fail loud, never degrade silently
        worktree_path_val = None
        worktree_failure = exc

    if not worktree_path_val and not no_isolation:
        # Failing loud (Fixes #29): an agent must not run in the operator's
        # real working tree because isolation was unavailable. This is the
        # state every greenfield repo starts in (no commits → unborn HEAD),
        # and it requires a human decision, not a warning.
        if isinstance(worktree_failure, WorktreeIsolationError):
            print(f"Error: {worktree_failure}", file=sys.stderr)
        elif worktree_failure is not None:
            print(f"Error: Worktree creation failed: {worktree_failure}", file=sys.stderr)
        else:
            print("Error: Task worktree could not be established.", file=sys.stderr)
        if existing_wt:
            print(
                "  A pre-created worktree (SNODO_WORKTREE_PATH) could not be used.",
                file=sys.stderr,
            )
        print(
            "  Task isolation is required by default. Re-run with --no-isolation "
            "only if you explicitly accept that the agent writes to your current "
            "working tree.",
            file=sys.stderr,
        )
        audit_log = getattr(args, "audit_log", None)
        if audit_log:
            try:
                audit_log.append_event("worktree_isolation_failed", {
                    "op": "worktree_isolation_failed",
                    "task_ref": task.id,
                    "reason": str(worktree_failure),
                })
            except Exception as e:
                _logger.warning(
                    "Could not record worktree_isolation_failed audit event: %s", e,
                )
        if checkpointer:
            _close_checkpointer(checkpointer)
        return 1

    worktree_degraded = False
    if worktree_path_val:
        print(f"  Worktree: {worktree_path_val}")
        # A task spec that cites a path the worktree cannot see is a spec whose
        # authority is silently transferred to the coder: the coder writes its
        # own version of the file and the validators judge the work against the
        # document the coder just authored (issue #93). Warn before dispatch —
        # not halt, because specs legitimately name paths that are meant to be
        # created, and only the operator can tell the two apart.
        from snodo.infrastructure.worktree import check_spec_paths_exist
        spec_for_paths = getattr(task, "root_spec", None) or task.spec
        missing = check_spec_paths_exist(project_root, spec_for_paths, worktree=worktree_path_val)
        if missing:
            print(
                "  Warning: the task spec cites paths that do not exist in the "
                "task worktree:",
                file=sys.stderr,
            )
            for path in missing:
                print(f"    - {path}", file=sys.stderr)
            print(
                "  The coder cannot see these and will invent its own versions, "
                "and validators will judge the work against what the coder "
                "authored. If a cited file exists in your working tree but is "
                "untracked, commit it so the worktree inherits it; if the path "
                "is meant to be created by the task, ignore this warning.",
                file=sys.stderr,
            )
    elif existing_wt:
        print("  Worktree: pre-created worktree not found — running without isolation")
        worktree_degraded = True
    else:
        print("  WARNING: Worktree creation failed — running WITHOUT isolation.")
        print("  WARNING: No task branch will be created. Files change current working tree.")
        worktree_degraded = True

    compiled_graph = _build_graph(
        args, protocol, project_root, model, checkpointer,
        audit_log=audit_log, session_manager=session_manager,
        session_id=session_id, job_id=job_id,
        worktree_path=worktree_path_val,
        worktree_degraded=worktree_degraded,
    )
    if compiled_graph is None:
        if worktree_path_val and not getattr(args, "retain_worktree", False):
            remove_worktree(project_root, task.id)
        elif worktree_path_val:
            _print_worktree_retained(project_root, task, worktree_path_val)
        if checkpointer:
            _close_checkpointer(checkpointer)
        return 1

    root_task_dict = {
        "id": task.id,
        "spec": task.spec,
        "parent_task_ref": task.parent_task_ref,
        "root_task_ref": getattr(task, "root_task_ref", None),
        "root_spec": getattr(task, "root_spec", None),
        "prior_failures": getattr(task, "prior_failures", []) or [],
        "attempt_provenance": getattr(task, "attempt_provenance", []) or [],
        "attempt_reads": getattr(task, "attempt_reads", []) or [],
        "depth": getattr(task, "depth", 0) or 0,
        "flow_type": getattr(task, "flow_type", None),
        "wave_id": getattr(task, "wave_id", None),
    }

    preserve_worktree = False
    merged_branch = None
    retain_worktree = bool(getattr(args, "retain_worktree", False))
    try:
        from snodo.engine.closure import run_to_closure

        max_attempts = getattr(protocol.execution, "max_total_fix_attempts", 10)
        max_depth = (
            protocol.max_recovery_depth_for(mode)
            if hasattr(protocol, "max_recovery_depth_for")
            else getattr(protocol.execution, "max_recovery_depth", 3)
        )
        final_state, closure_tree = run_to_closure(
            compiled_graph,
            root_task_dict,
            mode=mode,
            audit_log=audit_log,
            max_total_fix_attempts=max_attempts,
            max_recovery_depth=max_depth,
            session_id=session_id,
            thread_config=thread_config,
        )
        if memory_mgr:
            project_name = Path(project_root).name
            memory_mgr.record_task(project_name, mode)

        resolved = closure_tree is not None and closure_tree.outcome == "resolved"

        # A resolved closure that will not merge leaves the completed work on a
        # task branch while the base branch never moves. Say so — naming the
        # branch and the reason — before the follow-up block, so a run whose work
        # is stranded is never mistaken for one whose work landed.
        if resolved and not _should_auto_merge(
            protocol, mode, closure_tree, worktree_path_val, worktree_degraded
        ):
            _report_unmerged_branch(
                project_root, task, protocol, mode, closure_tree,
                worktree_path_val, worktree_degraded, session_id, audit_log,
            )

        result = _report_closure(closure_tree, final_state, session_id=session_id)

        halt_payload = _find_terminal_halt_payload(closure_tree, final_state)
        _record_task_completion(
            project_root, task.id, "completed" if resolved else "failed", halt_payload
        )

        # Auto-merge on genuine completion (closure outcome "resolved").
        if _should_auto_merge(protocol, mode, closure_tree, worktree_path_val, worktree_degraded):
            result, preserve_worktree, merged_branch = _merge_on_success(
                project_root, task, result, session_id, audit_log,
            )

        # Preserve the worktree on non-completion (so the evidence survives) or
        # when the retain flag is set. A cleanly completed task is torn down.
        if worktree_path_val and (retain_worktree or not resolved):
            preserve_worktree = True

        return result
    finally:
        # Save session checkpoint on exit
        if session_id and session_manager:
            try:
                session_manager.save_checkpoint(session_id)
            except Exception as e:
                _logger.warning(
                    "Could not save session checkpoint %s on exit: %s",
                    session_id, e,
                )
        # Clean up worktree, or leave it for inspection.
        if worktree_path_val:
            if preserve_worktree:
                _print_worktree_retained(project_root, task, worktree_path_val)
            else:
                try:
                    remove_worktree(project_root, task.id)
                except Exception as e:
                    _logger.warning(
                        "Could not remove worktree for task %s: %s", task.id, e,
                    )
        # Delete the task branch after the worktree is gone (a branch checked
        # out in a worktree cannot be deleted until that worktree is removed).
        if merged_branch:
            delete_task_branch(project_root, merged_branch)
        _close_checkpointer(checkpointer)

        # Fire-and-forget cloud sync (background thread, never blocks)
        if session_id and audit_log:
            try:
                from snodo.infrastructure.cloud_sync import sync_if_enabled
                sync_if_enabled(session_id, project_root, audit_log)
            except Exception as e:
                _logger.warning("Cloud sync hook failed: %s", e)

        if old_project_root is not None:
            os.environ["SNODO_PROJECT_ROOT"] = old_project_root
        else:
            os.environ.pop("SNODO_PROJECT_ROOT", None)


def _print_worktree_retained(project_root, task, worktree_path_val) -> None:
    """Tell the user where the retained worktree is and how to inspect/remove it."""
    from snodo.infrastructure.worktree import task_branch_name
    spec_for_branch = getattr(task, "root_spec", None) or task.spec
    branch = task_branch_name(task.id, spec_for_branch)
    print()
    print(f"Worktree preserved for inspection: {worktree_path_val}")
    print(f"  Branch: {branch}")
    print(f"  Inspect: snodo task show {task.id}")
    print(f"  List/remove: snodo worktree list / snodo worktree remove {task.id}")


def _auto_merge_block_reason(protocol, mode, closure_tree, worktree_path_val, worktree_degraded):
    """Return None when the branch should be merged, else the reason it will not be.

    Tests the three conditions in order and names the first that blocks the
    merge: auto-merge not enabled for this mode (protocol + mode override), the
    closure not genuinely resolved, and isolation degraded (no worktree was
    created — the work is already in the working tree, so there is no branch to
    merge). Used by both the decision (``_should_auto_merge``) and the loud
    report of a resolved-but-unmerged branch, so the reason a run shows is the
    same reason the decision used.
    """
    if not getattr(protocol, "auto_merge_enabled", lambda _m: False)(mode):
        return f"auto-merge not enabled for mode '{mode}'"
    if closure_tree is None or closure_tree.outcome != "resolved":
        return "closure not resolved"
    if worktree_degraded:
        return "isolation degraded (no task worktree — work is in the working tree)"
    if not worktree_path_val:
        return "no task worktree (work is in the working tree)"
    return None


def _should_auto_merge(protocol, mode, closure_tree, worktree_path_val, worktree_degraded) -> bool:
    """Decide whether a completed task's branch should be merged.

    Requires: auto-merge enabled for the mode (protocol + mode override), the
    closure genuinely resolved, and real isolation (a worktree was created —
    if it was not, the work is already in the working tree and there is nothing
    to merge). See ``_auto_merge_block_reason`` for the per-condition reasons.
    """
    return _auto_merge_block_reason(
        protocol, mode, closure_tree, worktree_path_val, worktree_degraded
    ) is None


def _report_unmerged_branch(project_root, task, protocol, mode, closure_tree,
                            worktree_path_val, worktree_degraded, session_id, audit_log) -> None:
    """Report a resolved task whose branch was NOT merged, and audit it.

    A resolved closure that does not merge leaves the completed work on a task
    branch while the base branch never moves — indistinguishable from a run
    whose work landed unless it says so. This prints the branch and the reason
    (the same reason ``_auto_merge_block_reason`` used) and records a
    ``task_unmerged`` event, so an unmerged run is distinguishable afterwards.
    """
    reason = _auto_merge_block_reason(
        protocol, mode, closure_tree, worktree_path_val, worktree_degraded
    )
    if reason is None:
        return

    from snodo.infrastructure.worktree import task_branch_name

    spec_for_branch = getattr(task, "root_spec", None) or task.spec
    branch = task_branch_name(task.id, spec_for_branch)
    print("⚠ Task resolved but its work was NOT merged to the base branch.", file=sys.stderr)
    print(f"  Reason: {reason}", file=sys.stderr)
    if worktree_degraded or not worktree_path_val:
        print(
            "  Isolation was degraded, so the changes are in your working tree; "
            "commit them there.",
            file=sys.stderr,
        )
    else:
        print(f"  Branch holding the work: {branch}", file=sys.stderr)
        print(
            f"  main has NOT moved. Merge it with: git merge {branch}",
            file=sys.stderr,
        )
    if audit_log:
        audit_log.append_event("task_unmerged", {
            "op": "task_unmerged",
            "task_ref": task.id,
            "branch": branch,
            "reason": reason,
            "merged": False,
            "session_id": session_id,
        })


def _verified_commit_matches_merge_target(stored_commit: str, target_commit: str) -> bool:
    """Whether a verification event's stored commit evidences the merge target.

    Real payloads carry a full SHA, so exact equality is the intended match. A
    stored value that merely abbreviates the target (a prefix of it) is also
    accepted. The reverse direction is deliberately NOT accepted: a stored
    value that has the target as its own prefix could denote a different,
    longer commit, so it must not satisfy the gate (Refs #206).
    """
    if not stored_commit or not target_commit:
        return False
    return stored_commit == target_commit or target_commit.startswith(stored_commit)


def _merge_on_success(project_root, task, result, session_id, audit_log) -> tuple:
    """Merge the completed task's branch into the base branch.

    Returns (result, preserve_worktree, merged_branch). On a clean merge the
    branch is queued for deletion (after the worktree is removed) and the
    worktree is left for the caller's normal teardown. On a conflict the task
    is escalated: the branch and worktree survive for a human to resolve.
    """
    from snodo.infrastructure.worktree import task_branch_name, merge_task_branch, merge_head_sha
    from snodo.tools.git import GitError

    spec_for_branch = getattr(task, "root_spec", None) or task.spec
    branch = task_branch_name(task.id, spec_for_branch)

    # Resolve target commit on the branch to be merged
    target_commit = ""
    try:
        from git import Repo
        repo = Repo(str(Path(project_root)), search_parent_directories=True)
        target_commit = repo.commit(branch).hexsha
    except Exception as e:
        _logger.debug("Could not resolve commit for branch %s: %s", branch, e)

    if audit_log:
        history = audit_log.get_history("verification_executed")
        matching = [
            e for e in history
            if (e.data.get("task_ref") == task.id or (getattr(task, "root_task_ref", None) and e.data.get("task_ref") == task.root_task_ref))
            and target_commit
            and _verified_commit_matches_merge_target(e.data.get("commit"), target_commit)
        ]
        matching_passes = [e for e in matching if e.data.get("outcome") == "pass"]
        matching_ungated = [e for e in matching if e.data.get("outcome") == "no_tests"]
        if not matching_passes and not matching_ungated:
            commit_display = target_commit[:7] if target_commit else "unknown"
            print(f"✗ Refused merge for {branch}: no passing verification_executed event for task {task.id} at commit {commit_display}.", file=sys.stderr)
            print("  An unverified merge is forbidden. Worktree and branch left intact.", file=sys.stderr)
            audit_log.append_event("unverified_merge_blocked", {
                "op": "unverified_merge_blocked",
                "task_ref": task.id,
                "branch": branch,
                "target_commit": target_commit,
                "reason": f"No passing verification_executed event recorded for task {task.id} at commit {commit_display}.",
                "session_id": session_id,
            })
            return 1, True, None

        if matching_passes:
            accepted_event = matching_passes[-1]
            commit_display = target_commit[:7] if target_commit else "unknown"
            cmd = accepted_event.data.get("command", "")
            print(f"✓ Verified merge for {branch}: task {task.id} verified at commit {commit_display} ({cmd}).", file=sys.stderr)
        else:
            # No genuine pass exists, but the audit trail explicitly records the
            # task ran ungated (outcome "no_tests"): the operator's configured
            # default test command executed and no tests were run. The merge
            # proceeds (a fresh project must not strand its first task) but the
            # line says so plainly, so a merge on unexecuted tests is never
            # mistaken for a verified one.
            commit_display = target_commit[:7] if target_commit else "unknown"
            print(f"✓ Merged {branch} ungated: task {task.id} at commit {commit_display} ran no tests (no test_command configured).", file=sys.stderr)

    try:
        res = merge_task_branch(project_root, branch)
        if isinstance(res, tuple):
            outcome, conflicting_paths = res
        else:
            outcome, conflicting_paths = res, []
    except GitError as e:
        print(f"✗ Merge failed for {branch}: {e}", file=sys.stderr)
        print("  The branch and worktree were left intact for manual resolution.", file=sys.stderr)
        if audit_log:
            audit_log.append_event("merge_failed_escalated", {
                "op": "merge_failed_escalated",
                "task_ref": task.id,
                "branch": branch,
                "error": str(e),
                "session_id": session_id,
            })
        return 1, True, None

    if outcome == "merged":
        if audit_log:
            authoritative_spec = getattr(task, "root_spec", None) or getattr(task, "spec", "")
            audit_log.append_event("task_merged", {
                "op": "task_merged",
                "task_ref": task.id,
                "branch": branch,
                "merge_sha": merge_head_sha(project_root),
                "session_id": session_id,
                "spec": authoritative_spec,
            })
        print(f"✓ Merged {branch} into the base branch")
        return result, False, branch

    paths_str = ", ".join(conflicting_paths) if conflicting_paths else "unknown path(s)"
    print(f"✗ Merge conflict merging {branch} into the base branch.", file=sys.stderr)
    print(f"  Conflicting path(s): {paths_str}", file=sys.stderr)
    print("  The merge was rolled back (base branch left clean; source branch intact).", file=sys.stderr)
    print(f"  To perform the merge manually and resolve conflicts, run:\n    git merge {branch}", file=sys.stderr)
    if audit_log:
        audit_log.append_event("merge_conflict_escalated", {
            "op": "merge_conflict_escalated",
            "task_ref": task.id,
            "branch": branch,
            "conflicting_paths": conflicting_paths,
            "session_id": session_id,
        })
    return 1, True, None


def _resolve_session(args, session_manager, protocol, project_root):
    """Resolve session: explicit resume, auto-resume, or auto-create.

    Reads current_mode from .snodo/state.json (HI-CTRL mode state),
    falling back to protocol.initial_mode.

    Args:
        args: CLI args (may have .resume, .mode attributes)
        session_manager: SessionManager instance (may be None)
        protocol: Protocol specification
        project_root: Absolute path to project root

    Returns:
        (SessionState, mode) tuple. The mode is the single resolved active
        mode; the session is None when session management is unavailable.
    """
    from snodo.infrastructure.state import read_state
    from snodo.project import get_project_id
    state = read_state(project_root)
    mode = getattr(args, "mode", None) or state.current_mode or protocol.initial_mode

    if session_manager is None:
        return None, mode

    resume_id = getattr(args, "resume", None)
    if resume_id:
        # Explicit resume: validate mode and project
        session = session_manager.load_session(resume_id)
        if session.mode != mode:
            print(f"Error: Session mode '{session.mode}' does not match "
                  f"current mode '{mode}'", file=sys.stderr)
            raise SystemExit(1)
        if session.project_root != project_root:
            print(f"Error: Session project '{session.project_root}' does not "
                  f"match current project '{project_root}'", file=sys.stderr)
            raise SystemExit(1)
        audit_log = getattr(args, "audit_log", None) or getattr(session_manager, "audit_log", None)
        if audit_log:
            audit_log.append_event("session_resumed", {
                "op": "session_resumed",
                "session_id": resume_id,
                "parent_checkpoint_ts": session.checkpoint.timestamp,
            })
            pid, scope = get_project_id(project_root)
            audit_log.append_event("project_announced", {
                "op": "project_announced",
                "project_id": pid,
                "scope": scope,
                "display_name": Path(project_root).name,
            })
        print(f"  Session: {resume_id} (resumed)")
        print(f"  Inspect: snodo session show {resume_id}")
        return session, mode

    # Auto: check for existing session (matching mode + project)
    existing = session_manager.get_active_session(mode, project_root)
    if existing:
        audit_log = getattr(args, "audit_log", None) or getattr(session_manager, "audit_log", None)
        if audit_log:
            audit_log.append_event("session_resumed", {
                "op": "session_resumed",
                "session_id": existing.session_id,
                "parent_checkpoint_ts": existing.checkpoint.timestamp,
            })
            pid, scope = get_project_id(project_root)
            audit_log.append_event("project_announced", {
                "op": "project_announced",
                "project_id": pid,
                "scope": scope,
                "display_name": Path(project_root).name,
            })
        print(f"  Session: {existing.session_id}")
        print(f"  Inspect: snodo session show {existing.session_id}")
        return existing, mode

    # Auto-create new session
    session = session_manager.create_session(mode, project_root)
    print(f"  Session: {session.session_id} (new)")
    print(f"  Inspect: snodo session show {session.session_id}")
    return session, mode


def _setup_memory(project_root: str, protocol: Protocol, mode: str):
    """Set up agent memory manager, checkpointer, and thread config.

    Returns:
        (memory_mgr, checkpointer, thread_config) tuple.
        Any or all may be None if memory setup fails gracefully.
    """
    try:
        from snodo.infrastructure.memory import AgentMemoryManager
        memory_mgr = AgentMemoryManager()
        project_name = Path(project_root).name
        agent = memory_mgr.get_or_create_agent(project_name, mode)
        checkpointer = memory_mgr.get_checkpointer()
        thread_config = {"configurable": {"thread_id": agent["thread_id"]}}
        return memory_mgr, checkpointer, thread_config
    except Exception as e:
        _logger.warning("Memory setup failed; continuing without memory: %s", e)
        return None, None, None


def _close_checkpointer(checkpointer) -> None:
    """Close checkpointer's underlying database connection."""
    if checkpointer is None:
        return
    try:
        if hasattr(checkpointer, "conn"):
            checkpointer.conn.close()
    except Exception as e:
        _logger.debug("Could not close checkpointer connection: %s", e)


def _build_graph(args, protocol: Protocol, project_root: str, model: str,
                 checkpointer=None, audit_log=None, session_manager=None,
                 session_id=None, job_id=None, worktree_path=None,
                 worktree_degraded=False):
    """Build and compile the protocol execution graph.

    Returns:
        Compiled graph, or None on failure.
    """
    from snodo.engine.loop import build_protocol_graph
    try:
        mcp_root = worktree_path or project_root
        use_mock = getattr(args, "mock", False)
        from snodo.coders import resolve_coder_name
        from snodo.compiler.models import Protocol as _Protocol
        mode_coder = None
        if isinstance(protocol, _Protocol):
            initial_mode_obj = protocol.get_mode(protocol.initial_mode)
            mode_coder = getattr(initial_mode_obj, "coder", None) if initial_mode_obj else None
        coder_name = resolve_coder_name(
            model=model,
            mode_coder=mode_coder,
            cli_coder=getattr(args, "coder", None),
            use_mock=use_mock,
        )
        print("Building execution graph with MCP services...")
        print(f"  Project root: {project_root}")
        print(f"  MCP root: {mcp_root}")
        print("  MCPs: workspace, git, shell")
        print(f"  Coder: {coder_name}")
        if checkpointer:
            print("  Memory: persistent (SqliteSaver)")
        print()

        graph = build_protocol_graph(
            protocol,
            project_root=project_root,
            use_mock_coder=use_mock,
            model=model,
            coder_name=getattr(args, "coder", None),
            checkpointer=checkpointer,
            audit_log=audit_log,
            session_manager=session_manager,
            session_id=session_id,
            job_id=job_id,
            worktree_path=worktree_path,
            worktree_degraded=worktree_degraded,
            verbose=getattr(args, "verbose", False),
        )
        compiled_graph = graph.compile(checkpointer=checkpointer)
        print("✓ Graph compiled with MCP integration")
        print()
        return compiled_graph
    except (AttributeError, TypeError):
        raise
    except Exception as e:
        print(f"Error: Failed to build graph: {e}", file=sys.stderr)
        if getattr(args, "verbose", False):
            import traceback
            traceback.print_exc()
        return None


def _report_closure(tree, final_state: dict, session_id: Optional[str] = None) -> int:
    """Print the aggregate closure result, emit the structured halt payload,
    and return the exit code.

    The structured halt payload is sourced from the engine (single source of
    truth) — the CLI never re-derives it.
    """
    from snodo.engine.closure import ClosureNode

    def _format_outcome(outcome: str) -> str:
        if outcome == "resolved":
            return "resolved"
        elif outcome == "recovery_exhausted":
            return "recovery_exhausted"
        elif outcome == "blocked" or outcome == "validator_error":
            return outcome
        elif outcome == "escalated":
            return "escalated"
        elif outcome == "internal_error":
            return "internal_error"
        return outcome

    def _print_tree(node: ClosureNode, indent: str = "") -> None:
        outcome_fmt = _format_outcome(node.outcome)
        print(f"{indent}{node.task_id}  {outcome_fmt}  (depth={node.depth})")
        for child in node.subtasks:
            _print_tree(child, indent + "  ")

    print()
    print("=" * 60)
    print("CLOSURE RESULT")
    print("-" * 60)
    _print_tree(tree)
    print("-" * 60)
    print(f"  Total attempts: {tree.attempts_used}")
    print()

    # Single emission site: emit the terminal halt's structured payload.
    # Per-subtask halts are not re-emitted — the closure tree above lists each,
    # and each subtask's payload is already in its own session checkpoint.
    halt_payload = _find_terminal_halt_payload(tree, final_state)
    if halt_payload is not None:
        print("--- STRUCTURED HALT PAYLOAD ---")
        print(json.dumps(halt_payload, indent=2, default=str))
        print("--- END STRUCTURED HALT PAYLOAD ---")
        print()
        _print_halt_followup(halt_payload, session_id)

        # The structured payload is the SINGLE authoritative outcome.  Never
        # print a second, unclassified outcome line after it (Fixes #66): in a
        # recovery chain the root's final_state carries no `error` field (the
        # error lives in the subtask's payload), so the legacy fallback below
        # would print "unknown internal error" — one run, two outcomes, the
        # second unclassified.
        decision = halt_payload.get("final_decision")
        if decision == "completed":
            return 0
        return 1

    # No structured payload — the failure happened outside the graph (e.g. the
    # closure driver's own failure state, or a graph that returned without
    # reaching a terminal node).  Fall back to the legacy classification.
    if tree.outcome == "internal_error" or final_state.get("halt_type") == "internal_error":
        err = final_state.get("error", "unknown internal error")
        print(f"✗ Internal error during execution: {err}", file=sys.stderr)
        return 1

    if tree.outcome == "resolved":
        is_blocked = final_state.get("is_blocked", False)
        if not is_blocked:
            artifacts = final_state.get("artifacts", [])
            print("✓ Task completed successfully!")
            if artifacts:
                print(f"  Artifacts ({len(artifacts)}):")
                for artifact in artifacts:
                    print(f"    - {artifact}")
            return 0
    print("✗ Task did not complete successfully", file=sys.stderr)
    return 1


def _print_halt_followup(halt_payload: dict, session_id: Optional[str]) -> None:
    """Print inspect/retry commands for the ids in a halt payload."""
    task_id = (halt_payload or {}).get("task_id", "")
    final_decision = (halt_payload or {}).get("final_decision")

    commands = []
    if session_id:
        commands.append(f"snodo session show {session_id}")
    if task_id:
        commands.append(f"snodo task show {task_id}")
        if final_decision not in ("completed", None):
            commands.append(f'snodo run --retry {task_id} "revised spec"')

    if not commands:
        return
    print("Follow-up:")
    for cmd in commands:
        print(f"  {cmd}")
    print()


def _find_terminal_halt_payload(tree, final_state: dict) -> Optional[dict]:
    """Return the halt payload for the terminal halt that produced the exit.

    Multi-halt semantics: a closure run can contain several halted nodes. Emit
    the DEEPEST non-completed halt payload (the one whose outcome propagated
    non-"resolved" to the root). Falls back to the root final state's payload.

    A resolved-through-recovery tree is the special case: the root's graph
    invocation ends at the ``recovery`` node, which writes a payload with
    ``final_decision: "completed"`` but ``phase: "unknown"`` and the FIRST
    attempt's validator results (Fixes #85). The genuine completion lives in
    the resolving subtask's payload (``phase: "complete"``). When the tree
    resolved, prefer the deepest genuine-completion payload over the root's
    recovery-node payload, so the printed verdicts belong to the attempt that
    resolved, not the one that failed.
    """
    from snodo.engine.closure import ClosureNode

    best: Optional[dict] = None
    best_depth = -1
    best_complete: Optional[dict] = None
    best_complete_depth = -1

    def walk(node: ClosureNode) -> None:
        nonlocal best, best_depth, best_complete, best_complete_depth
        p = node.halt_payload
        if p:
            decision = p.get("final_decision")
            if decision not in ("completed", None):
                if best is None or node.depth > best_depth:
                    best = p
                    best_depth = node.depth
            elif p.get("phase") == "complete":
                # A genuine completion payload (the resolving attempt's).
                if best_complete is None or node.depth > best_complete_depth:
                    best_complete = p
                    best_complete_depth = node.depth
        for child in node.subtasks:
            walk(child)

    walk(tree)
    if best is not None:
        return best
    if best_complete is not None:
        return best_complete
    meta = (final_state or {}).get("metadata") or {}
    return meta.get("halt_payload")


def _record_task_start(project_root: str, task_id: str, spec: str) -> None:
    """Record initial task metadata under .snodo/tasks/<task_id>/state.json."""
    try:
        from snodo.infrastructure.state import atomic_update_json

        task_dir = Path(project_root) / ".snodo" / "tasks" / task_id

        def _update(state: dict) -> None:
            state["task_id"] = task_id
            state["description"] = spec
            if "created_at" not in state:
                state["created_at"] = time.time()
            state["started_at"] = time.time()
            state["status"] = "running"

        atomic_update_json(task_dir, "state.json", _update)
    except Exception as e:
        _logger.debug("Could not record task start: %s", e)


def _record_task_completion(
    project_root: str,
    task_id: str,
    status: str,
    halt_payload: Optional[dict] = None,
) -> None:
    """Record final task completion and halt payload under .snodo/tasks/<task_id>/state.json."""
    try:
        from snodo.infrastructure.state import atomic_update_json

        task_dir = Path(project_root) / ".snodo" / "tasks" / task_id

        def _update(state: dict) -> None:
            state["task_id"] = task_id
            state["completed_at"] = time.time()
            state["status"] = status
            if halt_payload:
                state["halt"] = halt_payload

        atomic_update_json(task_dir, "state.json", _update)
    except Exception as e:
        _logger.debug("Could not record task completion: %s", e)


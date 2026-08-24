"""Run command - Execute tasks through protocol loop.

FILE: snodo/cli/commands/run_cmd.py
"""

import json
import logging
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

import typer

from snodo.compiler.models import Protocol
from snodo.core.interfaces import Task
from snodo.config import ConfigManager, provider_env
from snodo.cli.commands import load_protocol


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
        model: Optional[str] = typer.Option(
            None, "--model", "-m", help="Model to use (e.g., claude-sonnet-4-20250514, gpt-4)",
        ),
        verbose: bool = typer.Option(False, "--verbose", help="Show detailed output"),
        mock: bool = typer.Option(False, "--mock", help="Use mock coder instead of real LLM"),
        plan: Optional[str] = typer.Option(
            None, "--plan", "-p", help="Execute a plan by name",
        ),
        wave: Optional[int] = typer.Option(
            None, "--wave", "-w", help="Execute only a specific wave (requires --plan)",
        ),
        interactive: bool = typer.Option(
            False, "--interactive", "-i", help="Confirm each task before execution",
        ),
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
    ):
        """Execute a task through the protocol."""
        args = SimpleNamespace(
            description=description, protocol=protocol, model=model,
            verbose=verbose, mock=mock, plan=plan, wave=wave,
            interactive=interactive, from_pr=from_pr, background=background,
            sandbox=sandbox, resume=resume, retry=retry,
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
    except Exception:
        pass
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

    protocol_path = Path(args.protocol)
    if not protocol_path.is_absolute():
        protocol_path = Path(project_root) / args.protocol
    protocol = load_protocol(protocol_path)
    if not protocol:
        return 1

    mgr = ConfigManager()
    model = args.model or mgr.get_model()

    print(f"✓ Loaded protocol: {protocol.name}")
    print(f"  Modes: {', '.join(m.mode_id for m in protocol.modes)}")
    print(f"  Validators: {', '.join(v.validator_id for v in protocol.validators)}")
    print(f"  Policy: {protocol.disagreement_policy.value}")
    print(f"  Model: {model}")
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
    if not isinstance(task_failure, dict) or task_id not in task_failure:
        print(f"No failure context for {task_id}. Cannot retry.", file=sys.stderr)
        return 1

    failure = task_failure[task_id]
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
    original_spec = failure.get("spec", "")
    revised_spec = args.description

    failed_validators = failure.get("failed_validators", [])
    validator_details = "\n".join(
        f"  {v['validator_id']}: {v['justification']}"
        for v in failed_validators
    )
    files_changed = ", ".join(failure.get("files_changed", []))

    if revised_spec:
        augmented = (
            f"Original spec: {original_spec}\n\n"
            f"Revised spec (replaces original): {revised_spec}\n\n"
            f"Previous attempt {attempt} failed post-validation:\n"
            f"{validator_details}\n\n"
            f"Files changed in previous attempt: {files_changed}\n\n"
            f"Fix the issues above."
        )
    else:
        augmented = (
            f"Original spec: {original_spec}\n\n"
            f"Previous attempt {attempt} failed post-validation:\n"
            f"{validator_details}\n\n"
            f"Files changed in previous attempt: {files_changed}\n\n"
            f"Fix the issues above."
        )

    mgr = ConfigManager()
    model = args.model or mgr.get_model()

    task = Task(id=task_id, spec=augmented)
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
                except Exception:
                    pass

    # Set up git worktree — shared helper used by BOTH CLI inline and background
    from snodo.infrastructure.worktree import setup_for_task, remove_worktree, delete_task_branch
    existing_wt = os.environ.get("SNODO_WORKTREE_PATH")
    worktree_path_val = setup_for_task(project_root, task.id, task.spec, existing_worktree_path=existing_wt)
    worktree_degraded = False
    if worktree_path_val:
        print(f"  Worktree: {worktree_path_val}")
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
        if worktree_path_val:
            remove_worktree(project_root, task.id)
        if checkpointer:
            _close_checkpointer(checkpointer)
        return 1

    root_task_dict = {
        "id": task.id,
        "spec": task.spec,
        "parent_task_ref": task.parent_task_ref,
        "depth": task.depth,
    }

    preserve_worktree = False
    merged_branch = None
    try:
        from snodo.engine.closure import run_to_closure

        max_attempts = getattr(protocol.execution, "max_total_fix_attempts", 10)
        max_depth = getattr(protocol.execution, "max_recovery_depth", 3)
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

        result = _report_closure(closure_tree, final_state, session_id=session_id)

        # Auto-merge on genuine completion (closure outcome "resolved").
        if _should_auto_merge(protocol, mode, closure_tree, worktree_path_val, worktree_degraded):
            result, preserve_worktree, merged_branch = _merge_on_success(
                project_root, task, result, session_id, audit_log,
            )
        return result
    finally:
        # Save session checkpoint on exit
        if session_id and session_manager:
            try:
                session_manager.save_checkpoint(session_id)
            except Exception:
                pass
        # Clean up worktree (unless a failed/conflicting merge must survive).
        if worktree_path_val and not preserve_worktree:
            try:
                remove_worktree(project_root, task.id)
            except Exception:
                pass
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


def _should_auto_merge(protocol, mode, closure_tree, worktree_path_val, worktree_degraded) -> bool:
    """Decide whether a completed task's branch should be merged.

    Requires: auto-merge enabled for the mode (protocol + mode override), the
    closure genuinely resolved, and real isolation (a worktree was created —
    if it was not, the work is already in the working tree and there is nothing
    to merge).
    """
    if not getattr(protocol, "auto_merge_enabled", lambda _m: False)(mode):
        return False
    if closure_tree is None or closure_tree.outcome != "resolved":
        return False
    if worktree_degraded or not worktree_path_val:
        return False
    return True


def _merge_on_success(project_root, task, result, session_id, audit_log) -> tuple:
    """Merge the completed task's branch into the base branch.

    Returns (result, preserve_worktree, merged_branch). On a clean merge the
    branch is queued for deletion (after the worktree is removed) and the
    worktree is left for the caller's normal teardown. On a conflict the task
    is escalated: the branch and worktree survive for a human to resolve.
    """
    from snodo.infrastructure.worktree import task_branch_name, merge_task_branch
    from snodo.tools.git import GitError

    branch = task_branch_name(task.id, task.spec)
    try:
        outcome = merge_task_branch(project_root, branch)
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
            audit_log.append_event("task_merged", {
                "op": "task_merged",
                "task_ref": task.id,
                "branch": branch,
                "session_id": session_id,
            })
        print(f"✓ Merged {branch} into the base branch")
        return result, False, branch

    # Conflict — escalate, leave branch + worktree intact for a human.
    print(f"✗ Merge conflict merging {branch} into the base branch.", file=sys.stderr)
    print("  The branch and worktree were left intact for manual resolution.", file=sys.stderr)
    if audit_log:
        audit_log.append_event("merge_conflict_escalated", {
            "op": "merge_conflict_escalated",
            "task_ref": task.id,
            "branch": branch,
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
    state = read_state(project_root)
    mode = state.current_mode or protocol.initial_mode

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
        audit_log = getattr(args, "audit_log", None)
        if audit_log:
            audit_log.append_event("session_resumed", {
                "op": "session_resumed",
                "session_id": resume_id,
                "parent_checkpoint_ts": session.checkpoint.timestamp,
            })
        print(f"  Session: {resume_id} (resumed)")
        print(f"  Inspect: snodo session show {resume_id}")
        return session, mode

    # Auto: check for existing session (matching mode + project)
    existing = session_manager.get_active_session(mode, project_root)
    if existing:
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
    except Exception:
        return None, None, None


def _close_checkpointer(checkpointer) -> None:
    """Close checkpointer's underlying database connection."""
    if checkpointer is None:
        return
    try:
        if hasattr(checkpointer, "conn"):
            checkpointer.conn.close()
    except Exception:
        pass


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
        print("Building execution graph with MCP services...")
        print(f"  Project root: {project_root}")
        print(f"  MCP root: {mcp_root}")
        print("  MCPs: workspace, git, shell")
        print(f"  Coder: {'mock' if args.mock else 'real LLM'}")
        if checkpointer:
            print("  Memory: persistent (SqliteSaver)")
        print()

        graph = build_protocol_graph(
            protocol,
            project_root=project_root,
            use_mock_coder=args.mock,
            model=model,
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
    except Exception as e:
        print(f"Error: Failed to build graph: {e}", file=sys.stderr)
        if args.verbose:
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
    """
    from snodo.engine.closure import ClosureNode

    best: Optional[dict] = None
    best_depth = -1

    def walk(node: ClosureNode) -> None:
        nonlocal best, best_depth
        p = node.halt_payload
        if p and p.get("final_decision") not in ("completed", None):
            if best is None or node.depth > best_depth:
                best = p
                best_depth = node.depth
        for child in node.subtasks:
            walk(child)

    walk(tree)
    if best is not None:
        return best
    meta = (final_state or {}).get("metadata") or {}
    return meta.get("halt_payload")

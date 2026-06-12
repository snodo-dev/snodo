"""Run command - Execute tasks through protocol loop.

FILE: snodo/cli/commands/run_cmd.py
"""

import json
import logging
import sys
from dataclasses import is_dataclass, asdict
from pathlib import Path
from typing import Optional

from snodo.compiler.models import Protocol
from snodo.core.interfaces import Task
from snodo.engine.loop import build_protocol_graph, LoopStage
from snodo.cli.config import ConfigManager, _set_api_key_env
from snodo.cli.commands import load_protocol

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
    audit_log = get_audit_log()
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

    _set_api_key_env(mgr, model)

    description = _build_description(args)

    task = Task(
        id=f"task_{hash(description) & 0xffffff:06x}",
        spec=description
    )

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
    _set_api_key_env(mgr, model)

    task = Task(id=task_id, spec=augmented)
    print(f"Retrying task {task_id} (attempt {attempt + 1}/{max_retries})")
    print()

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
    print()

    from snodo.infrastructure.paths import require_project_root
    project_root = require_project_root()
    audit_log = getattr(args, "audit_log", None)
    session_manager = getattr(args, "session_manager", None)

    # Session lifecycle: start or resume
    session = _resolve_session(args, session_manager, protocol, project_root)
    session_id = session.session_id if session else None

    if session_id and session_manager:
        session_manager.set_current_task(session_id, task.id)

    # Set up agent memory
    memory_mgr, checkpointer, thread_config = _setup_memory(project_root, protocol)

    compiled_graph = _build_graph(
        args, protocol, project_root, model, checkpointer,
        audit_log=audit_log, session_manager=session_manager,
        session_id=session_id,
    )
    if compiled_graph is None:
        if checkpointer:
            _close_checkpointer(checkpointer)
        return 1

    initial_state = {
        "task": {"id": task.id, "spec": task.spec, "parent_task_ref": task.parent_task_ref, "depth": task.depth},
        "current_mode": protocol.initial_mode,
        "iteration": 0,
        "stage": "governance",
        "validation_results": [],
        "validation_token": None,
        "artifacts": [],
        "constraints_passed": True,
        "constraint_violations": [],
        "policy_decision": None,
        "pending_disagreement": None,
        "halt_type": None,
        "is_complete": False,
        "is_blocked": False,
        "metadata": {},
        "messages": [],
    }

    try:
        result = _stream_execution(compiled_graph, initial_state, args, thread_config)
        if memory_mgr:
            project_name = Path(project_root).name
            memory_mgr.record_task(project_name, protocol.initial_mode)
        return result
    finally:
        # Save session checkpoint on exit
        if session_id and session_manager:
            try:
                session_manager.save_checkpoint(session_id)
            except Exception:
                pass
        _close_checkpointer(checkpointer)

        # Fire-and-forget cloud sync (background thread, never blocks)
        if session_id and audit_log:
            try:
                from snodo.infrastructure.cloud_sync import sync_if_enabled
                sync_if_enabled(session_id, project_root, audit_log)
            except Exception as e:
                _logger.warning("Cloud sync hook failed: %s", e)


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
        SessionState or None if session management unavailable
    """
    if session_manager is None:
        return None

    from snodo.infrastructure.state import read_state
    state = read_state(project_root)
    mode = state.current_mode or protocol.initial_mode

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
        return session

    # Auto: check for existing session (matching mode + project)
    existing = session_manager.get_active_session(mode, project_root)
    if existing:
        print(f"  Session: {existing.session_id}")
        return existing

    # Auto-create new session
    session = session_manager.create_session(mode, project_root)
    print(f"  Session: {session.session_id} (new)")
    return session


def _setup_memory(project_root: str, protocol: Protocol):
    """Set up agent memory manager, checkpointer, and thread config.

    Returns:
        (memory_mgr, checkpointer, thread_config) tuple.
        Any or all may be None if memory setup fails gracefully.
    """
    try:
        from snodo.infrastructure.memory import AgentMemoryManager
        memory_mgr = AgentMemoryManager()
        project_name = Path(project_root).name
        agent = memory_mgr.get_or_create_agent(project_name, protocol.initial_mode)
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
                 session_id=None):
    """Build and compile the protocol execution graph.

    Returns:
        Compiled graph, or None on failure.
    """
    try:
        print("Building execution graph with MCP services...")
        print(f"  Project root: {project_root}")
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


def _serialize_policy_decision(pd: object) -> Optional[dict]:
    """Serialize a PolicyDecision to a dict-safe form.

    Delegates to the engine helper, with a local fallback for
    belt-and-suspenders safety (handles live dataclass instances
    that might still arrive before the checkpoint fix takes effect).
    """
    try:
        from snodo.engine.policy import policy_decision_to_dict
        return policy_decision_to_dict(pd)
    except ImportError:
        if pd is None:
            return None
        if isinstance(pd, dict):
            return pd
        if is_dataclass(pd) and not isinstance(pd, type):
            return asdict(pd)
        return {"value": str(pd)}


def _render_halt_payload(node_state: dict) -> dict:
    """Build a structured halt payload for any halt type.

    Returns the payload dict (also printed to stdout).
    The caller is responsible for printing the human summary
    before calling this function.

    halt_type is read from node_state if available; otherwise
    inferred from existing fields for backward compatibility.
    """
    import json

    halt_type = node_state.get("halt_type")
    pending = node_state.get("pending_disagreement")

    # Backward compat: infer halt_type if not set
    if not halt_type:
        if pending:
            halt_type = "escalated"
        elif node_state.get("constraint_violations"):
            halt_type = "constraint"
        else:
            halt_type = "blocked"

    task = node_state.get("task", {})
    reason = "; ".join(node_state.get("constraint_violations", [])) or "blocker"

    payload = {
        "status": "blocked",
        "halt_type": halt_type,
        "reason": reason,
        "task_id": task.get("id", ""),
        "task_spec": task.get("spec", ""),
        "iteration": node_state.get("iteration", 0),
        "current_mode": node_state.get("current_mode", ""),
        "validator_results": node_state.get("validation_results", []),
        "policy_decision": _serialize_policy_decision(node_state.get("policy_decision")),
        "hint": (
            "Address the blocking concerns and re-run a revised task. "
            "If you believe the block is incorrect, use "
            "`snodo authorize <task_id>`.\n"
            "Run: snodo authorize to list all pending decisions."
        ),
    }

    # Carry escalation-specific fields when present
    if pending:
        payload["phase"] = pending.get("phase")
        payload["policy"] = pending.get("policy")
        payload["escalation_validator_results"] = pending.get("validator_results", [])
        payload["escalation_policy_decision"] = pending.get("policy_decision", {})

    meta = node_state.get("metadata", {})
    payload["pre_validation"] = meta.get("pre_validation")
    payload["post_validation"] = meta.get("post_validation")
    payload["final_decision"] = "blocked"

    print()
    print("--- STRUCTURED HALT PAYLOAD ---")
    print(json.dumps(payload, indent=2))
    print("--- END STRUCTURED HALT PAYLOAD ---")
    print()

    if halt_type == "escalated" or (pending and halt_type != "escalated"):
        print("Run: snodo authorize <task_id>")

    return payload


def _print_stage(node_state: dict) -> None:
    """Print execution stage progress."""
    stage = node_state.get("stage", "unknown")
    iteration = node_state.get("iteration", 0)

    print(f"  [{iteration}] {stage}", end="")

    if stage == "validate":
        results = node_state.get("validation_results", [])
        if results:
            severities = [r.get("severity") for r in results]
            print(f" - {len(results)} validator(s): {', '.join(severities)}", end="")

    if stage == "execute":
        artifacts = node_state.get("artifacts", [])
        if artifacts:
            print(f" - {len(artifacts)} artifact(s) created", end="")

    print()


def _stream_execution(compiled_graph, initial_state: dict, args,
                      thread_config=None) -> int:
    """Stream graph execution and report progress.

    Args:
        compiled_graph: Compiled LangGraph
        initial_state: Initial state dict
        args: CLI args
        thread_config: Optional dict with configurable.thread_id for checkpointing

    Returns:
        0 on success, 1 on failure.
    """
    print("Executing task through protocol...")
    print("=" * 60)

    try:
        final_state = None
        stream_kwargs = {}
        if thread_config:
            stream_kwargs["config"] = thread_config
        for i, state in enumerate(compiled_graph.stream(initial_state, **stream_kwargs)):
            if not isinstance(state, dict):
                continue
            node_state = next(iter(state.values()))
            if not isinstance(node_state, dict) or "stage" not in node_state:
                continue

            _print_stage(node_state)

            if node_state.get("is_blocked"):
                halt_type = node_state.get("halt_type", "blocked")
                violations = node_state.get("constraint_violations", [])

                if halt_type == "escalated":
                    print("\n✗ ESCALATED (warn): validation failed unanimously")
                elif halt_type == "validator_error":
                    error_validators = [
                        r["validator_id"]
                        for r in node_state.get("validation_results", [])
                        if r.get("severity") == "error"
                    ]
                    names = ", ".join(error_validators) if error_validators else "unknown"
                    print(f"\n✗ VALIDATOR ERROR: {names} produced no verdict — resolve or retry")
                else:
                    print(f"\n✗ BLOCKED: {', '.join(violations) if violations else 'blocker'}")

                # Print validator justifications (blockers and warns)
                validation_results = node_state.get("validation_results", [])
                if validation_results:
                    blockers = [r for r in validation_results if r.get("severity") == "blocker"]
                    warns = [r for r in validation_results if r.get("severity") == "warn"]
                    if blockers:
                        print("\n  Validator blockers:")
                        for r in blockers:
                            print(f"    {r['validator_id']} — blocker — {r['justification']}")
                    if warns:
                        print("\n  Validator warnings:")
                        for r in warns:
                            print(f"    {r['validator_id']} — warn — {r['justification']}")

                # Emit structured halt payload for ALL halt types
                _render_halt_payload(node_state)
                return 1

            final_state = node_state

        print("=" * 60)
        return _report_result(final_state)

    except Exception as e:
        print(f"\nError during execution: {e}", file=sys.stderr)
        if args.verbose:
            import traceback
            traceback.print_exc()
        return 1


def _report_result(final_state: Optional[dict]) -> int:
    """Report task completion result.

    Returns:
        0 on success, 1 on failure.
    """
    if final_state and final_state.get("stage") == LoopStage.COMPLETE.value:
        artifacts = final_state.get("artifacts", [])
        print("\n✓ Task completed successfully!")
        print(f"  Iterations: {final_state.get('iteration', 0)}")
        if artifacts:
            print(f"  Artifacts ({len(artifacts)}):")
            for artifact in artifacts:
                print(f"    - {artifact}")
        return 0
    else:
        print("\n✗ Task did not complete successfully", file=sys.stderr)
        return 1

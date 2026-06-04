"""Run command - Execute tasks through protocol loop.

FILE: snodo/cli/commands/run_cmd.py
"""

import json
import sys
import os
from dataclasses import is_dataclass, asdict
from pathlib import Path
from typing import Optional

from snodo.compiler.models import Protocol
from snodo.core.interfaces import Task
from snodo.engine.loop import build_protocol_graph, LoopStage
from snodo.cli.config import ConfigManager
from snodo.cli.commands import load_protocol


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
    # Construct audit_log and session_manager at CLI top level (7.1/7.3 pattern)
    from snodo.infrastructure.audit import get_audit_log
    from snodo.infrastructure.session import SessionManager

    audit_log = get_audit_log()
    session_manager = SessionManager(audit_log=audit_log)
    args.audit_log = audit_log
    args.session_manager = session_manager

    if getattr(args, "background", False):
        return _submit_background_job(args)

    if getattr(args, "plan", None):
        return _run_plan(args)

    if args.description is None:
        print("Error: task description required (or use --plan <name>)", file=sys.stderr)
        return 1

    # Route through Docker sandbox if requested
    sandbox_type = getattr(args, "sandbox", "local")
    if sandbox_type == "docker":
        return _run_in_sandbox(args)

    protocol_path = Path(args.protocol)
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
        project_root = str(Path.cwd())
        print(f"Fetching PR #{from_pr} context...")
        pr_context = _fetch_pr_context(from_pr, project_root)
        description = f"{pr_context}\n\n{description}"
        print("  PR context prepended to task spec")
        print()
    return description


def _submit_background_job(args) -> int:
    """Submit task as a background job.

    Validates args, builds task_args dict, calls JobManager.submit(),
    prints job_id with helper commands.
    """
    from snodo.jobs import JobManager, JobError

    if getattr(args, "plan", None):
        print("Error: --plan and --background cannot be used together", file=sys.stderr)
        return 1

    if args.description is None:
        print("Error: task description required for background jobs", file=sys.stderr)
        return 1

    protocol_path = Path(args.protocol)
    if not protocol_path.exists():
        print(f"Error: Protocol file not found: {protocol_path}", file=sys.stderr)
        print("Run 'snodo init' to create default protocol.", file=sys.stderr)
        return 1

    # Set API key env vars so child process inherits them
    mgr = ConfigManager()
    model = args.model or mgr.get_model()
    _set_api_key_env(mgr, model)

    task_args = {
        "description": args.description,
        "protocol": args.protocol,
        "model": model,
        "mock": getattr(args, "mock", False),
        "verbose": getattr(args, "verbose", False),
        "from_pr": getattr(args, "from_pr", None),
        "cwd": str(Path.cwd()),
    }

    try:
        project_root = str(Path.cwd())
        manager = JobManager(project_root)
        job_id = manager.submit(task_args)
    except (ValueError, JobError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    print(f"Job submitted: {job_id}")
    print(f"  snodo job status {job_id}")
    print(f"  snodo job logs {job_id}")
    print(f"  snodo job wait {job_id}")
    return 0


def _build_sandbox_command(args) -> list:
    """Build the snodo run command for inside the container."""
    command = ["snodo", "run", args.description, "--protocol", args.protocol]
    if args.model:
        command.extend(["--model", args.model])
    if getattr(args, "mock", False):
        command.append("--mock")
    if getattr(args, "verbose", False):
        command.append("--verbose")
    from_pr = getattr(args, "from_pr", None)
    if from_pr:
        command.extend(["--from-pr", str(from_pr)])
    return command


def _build_sandbox_env(mgr: ConfigManager, model: str) -> dict:
    """Build environment variables (API keys) for the sandbox container."""
    env: dict[str, str] = {}
    api_key = mgr.get_key_for_model(model)
    if not api_key:
        return env
    env_map = {
        "claude-": "ANTHROPIC_API_KEY",
        "gpt-": "OPENAI_API_KEY",
        "o1-": "OPENAI_API_KEY",
        "o3-": "OPENAI_API_KEY",
        "gemini/": "GEMINI_API_KEY",
        "gemini-": "GEMINI_API_KEY",
    }
    for prefix, env_var in env_map.items():
        if model.startswith(prefix):
            env[env_var] = api_key
            break
    return env


def _print_sandbox_result(result, sandbox_image: str, config) -> None:
    """Print sandbox execution output and summary."""
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    print()
    print(f"Container: {result.container_id or 'N/A'}")
    print(f"Duration: {result.duration:.1f}s")
    print(f"Exit code: {result.exit_code}")


def _run_in_sandbox(args) -> int:
    """Execute task inside a Docker sandbox container.

    Builds the snodo run command and dispatches to DockerSandbox.
    Falls back to local execution if Docker is unavailable.
    """
    from snodo.sandbox import DockerSandbox, SandboxConfig

    sandbox = DockerSandbox()

    if not sandbox.is_available():
        print("Warning: Docker not available, falling back to local execution",
              file=sys.stderr)
        args.sandbox = "local"
        return run_command(args)

    if not sandbox.image_exists():
        print("Error: snodo-worker image not built", file=sys.stderr)
        print("Run: snodo sandbox build", file=sys.stderr)
        return 1

    mgr = ConfigManager()
    model = args.model or mgr.get_model()

    config = SandboxConfig(
        network="none",
        memory_limit="2g",
        cpu_limit=2.0,
        env=_build_sandbox_env(mgr, model),
    )

    print("Running in Docker sandbox...")
    print(f"  Image: {sandbox._image}")
    print(f"  Network: {config.network}")
    print(f"  Memory: {config.memory_limit}")
    print(f"  CPUs: {config.cpu_limit}")
    print()

    command = _build_sandbox_command(args)
    result = sandbox.run_task(command, Path.cwd(), config=config)

    _print_sandbox_result(result, sandbox._image, config)
    return result.exit_code


def _set_api_key_env(mgr: ConfigManager, model: str) -> None:
    """Set API key in environment if available from config."""
    api_key = mgr.get_key_for_model(model)
    if api_key:
        env_map = {
            "claude-": "ANTHROPIC_API_KEY",
            "gpt-": "OPENAI_API_KEY",
            "o1-": "OPENAI_API_KEY",
            "o3-": "OPENAI_API_KEY",
            "gemini/": "GEMINI_API_KEY",
            "gemini-": "GEMINI_API_KEY",
        }
        for prefix, env_var in env_map.items():
            if model.startswith(prefix):
                os.environ[env_var] = api_key
                break


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

    project_root = str(Path.cwd())
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
        "resolution_override": False,
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
            "`snodo resolve <session_id> <task_id> --decision proceed|halt`."
        ),
    }

    # Carry escalation-specific fields when present
    if pending:
        payload["phase"] = pending.get("phase")
        payload["policy"] = pending.get("policy")
        # pending_disagreement carries its own validator_results/policy_decision
        # that may differ from the top-level — include both for backward compat
        payload["escalation_validator_results"] = pending.get("validator_results", [])
        payload["escalation_policy_decision"] = pending.get("policy_decision", {})

    print()
    print("--- STRUCTURED HALT PAYLOAD ---")
    print(json.dumps(payload, indent=2))
    print("--- END STRUCTURED HALT PAYLOAD ---")
    print()

    if halt_type == "escalated" or (pending and halt_type != "escalated"):
        print("To resolve: snodo resolve <session_id> <task_id> --decision proceed|halt")

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


def _task_completed(tasks_status: dict, task_id: str) -> bool:
    """Check if a task is completed, handling both string and dict entries."""
    entry = tasks_status.get(task_id)
    if isinstance(entry, dict):
        return entry.get("status") == "completed"
    return entry == "completed"


def _get_completed_waves(waves: list, tasks_status: dict) -> set:
    """Determine which waves are fully completed.

    Args:
        waves: All waves from plan data
        tasks_status: Task status mapping

    Returns:
        Set of completed wave IDs
    """
    completed = set()
    for wave in waves:
        wid = wave.get("id")
        wave_tasks = wave.get("tasks", [])
        if wave_tasks and all(_task_completed(tasks_status, t) for t in wave_tasks):
            completed.add(wid)
    return completed


def _execute_wave_task(planner, args, protocol, model, wave_id, task_id) -> bool:
    """Execute a single task within a wave.

    Returns:
        True on success, False on failure.
    """
    wave_dir = planner.plans_dir / args.plan / f"wave_{wave_id}"
    spec_file = wave_dir / f"{task_id}_task.md"
    if not spec_file.exists():
        print(f"  [{task_id}] ERROR: spec file not found", file=sys.stderr)
        return False

    spec = spec_file.read_text()
    planner.update_status(args.plan, task_id, "in_progress")

    task = Task(id=task_id, spec=spec)
    print(f"  [{task_id}] executing...")
    result = _execute_task(args, protocol, task, model)

    if result == 0:
        planner.update_status(args.plan, task_id, "completed")
        return True
    else:
        planner.update_status(args.plan, task_id, "blocked")
        print(f"  [{task_id}] FAILED", file=sys.stderr)
        return False


def _filter_waves(waves: list, wave_filter) -> Optional[list]:
    """Filter waves by ID. Returns None on error."""
    if wave_filter is None:
        return waves
    filtered = [w for w in waves if w.get("id") == wave_filter]
    if not filtered:
        print(f"Error: Wave {wave_filter} not found in plan", file=sys.stderr)
        return None
    return filtered


def _run_plan(args) -> int:
    """Execute a plan's tasks through the protocol loop."""
    from snodo.mcp.planner import PlannerMCP, PlannerError

    protocol_path = Path(args.protocol)
    protocol = load_protocol(protocol_path)
    if not protocol:
        return 1

    mgr = ConfigManager()
    model = args.model or mgr.get_model()
    _set_api_key_env(mgr, model)

    try:
        audit_log = getattr(args, "audit_log", None)
        planner = PlannerMCP(str(Path.cwd()), audit_log=audit_log)
        plan_data = planner.get_plan(args.plan)
        status_data = planner.get_status(args.plan)
    except (ValueError, PlannerError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    print(f"Plan: {plan_data.get('name', args.plan)}")
    print(f"Intent: {plan_data.get('intent', 'N/A')}")
    print()

    waves = _filter_waves(plan_data.get("waves", []), getattr(args, "wave", None))
    if waves is None:
        return 1

    tasks_status = status_data.get("tasks", {})
    completed_waves = _get_completed_waves(plan_data.get("waves", []), tasks_status)
    interactive = getattr(args, "interactive", False)

    failed = _execute_waves(waves, planner, args, protocol, model,
                            tasks_status, completed_waves, interactive)

    _print_plan_progress(planner, args.plan)
    return 1 if failed else 0


def _print_plan_progress(planner, plan_name: str) -> None:
    """Print final plan progress."""
    status_data = planner.get_status(plan_name)
    tasks = status_data.get("tasks", {})
    done = sum(1 for s in tasks.values()
               if (s.get("status") if isinstance(s, dict) else s) == "completed")
    print(f"\nPlan progress: {done}/{len(tasks)} completed")


def _should_skip_task(task_id, tasks_status, interactive) -> bool:
    """Check if a task should be skipped (completed or user declined).

    Returns:
        True if the task should be skipped.
    """
    if _task_completed(tasks_status, task_id):
        print(f"  [{task_id}] skipped (completed)")
        return True
    if interactive:
        answer = input(f"  Execute {task_id}? [y/N] ").strip().lower()
        if answer != "y":
            print(f"  [{task_id}] skipped (user)")
            return True
    return False


def _execute_waves(waves, planner, args, protocol, model,
                   tasks_status, completed_waves, interactive) -> bool:
    """Execute waves in order, respecting dependencies.

    Returns:
        True if any task failed, False if all succeeded.
    """
    for wave in waves:
        wave_id = wave.get("id")
        deps = wave.get("depends_on", [])

        unmet = [d for d in deps if d not in completed_waves]
        if unmet:
            print(f"Wave {wave_id}: blocked (depends on: {', '.join(str(d) for d in unmet)})")
            continue

        print(f"Wave {wave_id}:")
        for task_id in wave.get("tasks", []):
            if _should_skip_task(task_id, tasks_status, interactive):
                continue
            if not _execute_wave_task(planner, args, protocol, model, wave_id, task_id):
                return True  # failed

    return False

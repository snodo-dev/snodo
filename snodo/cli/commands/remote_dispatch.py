"""Dispatch a task to an ADR 055 SSH worker and keep writes local."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

from snodo.cli.commands.remote_stream import (
    LocalRemoteStreamSink,
    RemoteStreamError,
    consume_remote_stream,
)
from snodo.remote_host import check_remote_host, resolve_host_path, select_execution_host

_PREFLIGHTS: set[tuple[str, str, str]] = set()


def prepare_remote_run(protocol: Any, project_root: str) -> str | None:
    """Resolve and preflight the host once for this local run process."""
    execution = protocol.execution
    host = select_execution_host(execution)
    if not host:
        return None
    host_path = getattr(execution, "host_path", None)
    key = (str(Path(project_root).resolve()), host, host_path or "")
    if key not in _PREFLIGHTS:
        checks = check_remote_host(host, project_root, host_path)
        failed = [check for check in checks if not check.ok]
        if failed:
            raise RuntimeError("Remote host preflight failed: " + "; ".join(
                f"{check.name}: {check.detail}" for check in failed
            ))
        _PREFLIGHTS.add(key)
    return host


def _git(project_root: str | Path, *args: str) -> str:
    result = subprocess.run(  # noqa: S603 - fixed git command with internal arguments
        ["git",  # noqa: S607 - git is resolved through PATH by design
         "-C", str(project_root), *args],
        capture_output=True, text=True, check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def _git_remote_url(host: str, host_path: str) -> str:
    # The scp-style URL preserves SSH's remote-shell expansion of a leading
    # ``~/``; the ``ssh://`` form treats it as a literal ``/~/`` directory.
    return f"{host}:{host_path}"


def _provider_keys(model: str) -> dict[str, str]:
    from snodo.config import ConfigManager

    manager = ConfigManager()
    providers = manager.get_providers()
    provider = ConfigManager._provider_for_model(model)
    if not provider:
        return {}
    config = providers.get(provider)
    if not config:
        return {}
    key = manager.get_key_for_model(model)
    return {config.api_key_env: key} if key and config.api_key_env else {}


def dispatch_remote_task(args, protocol, task, model: str, project_root: str) -> int | None:
    """Run *task* remotely; return None when no host is configured."""
    try:
        host = prepare_remote_run(protocol, project_root)
    except Exception as exc:
        print(f"Remote host preflight failed: {exc}", file=sys.stderr)
        return 1
    if host is None:
        return None

    from snodo.cli.commands import run_cmd
    from snodo.cli.commands.task_record import _record_task_start, _record_task_completion
    from snodo.infrastructure.worktree import task_branch_name

    execution = protocol.execution
    host_path = resolve_host_path(project_root, getattr(execution, "host_path", None))
    remote_repo = _git_remote_url(host, host_path)
    plan_name = run_cmd.worktree.task_plan_name(args)
    branch = task_branch_name(task.id, getattr(task, "root_spec", None) or task.spec, plan_name)
    base_sha = _git(project_root, "rev-parse", "HEAD")
    base_ref = f"refs/snodo/bases/{task.id}"
    # The remote clone's checked-out branch is never changed: the worker gets
    # its base object through a private ref and creates its own task worktree.
    job_id = os.environ.get("SNODO_JOB_ID")
    def status_writer(ref, status):
        _write_task_status(project_root, args, ref, status)
    sink = LocalRemoteStreamSink(
        project_root, job_id or f"remote-{task.id}", getattr(args, "audit_log", None), status_writer,
    )
    if getattr(args, "audit_log", None) is None:
        from snodo.infrastructure.audit import AuditLog
        sink.audit_log = AuditLog(str(Path(project_root) / ".snodo" / "audit.log"))

    _record_task_start(project_root, task.id, task.spec)
    if job_id:
        job_dir = Path(project_root) / ".snodo" / "jobs" / job_id
        task_path = job_dir / "task.json"
        if task_path.exists():
            try:
                data = json.loads(task_path.read_text())
                data["host"] = host
                task_path.write_text(json.dumps(data, indent=2) + "\n")
            except (OSError, ValueError):
                pass

    worker_args = [
        "snodo", "_worker", "--project-root", ".", "--task-id", task.id,
        "--spec", task.spec, "--protocol", getattr(args, "protocol", ".snodo/protocol.yml"),
        "--model", model, "--base-sha", base_ref,
    ]
    coder = getattr(args, "coder", None)
    mode = getattr(args, "mode", None)
    if coder:
        worker_args.extend(["--coder", coder])
    if mode:
        worker_args.extend(["--mode", mode])
    if plan_name:
        worker_args.extend(["--plan", plan_name])
    if getattr(args, "mock", False):
        worker_args.append("--mock")
    command = f"cd {shlex.quote(host_path)} && " + " ".join(map(shlex.quote, worker_args))
    keys = _provider_keys(model)
    try:
        subprocess.run(  # noqa: S603 - internal git ref and operator-selected SSH remote
            ["git",  # noqa: S607 - git is resolved through PATH by design
             "-C", project_root, "push", "--quiet", remote_repo, f"{base_sha}:{base_ref}"],
            capture_output=True, text=True, check=True,
        )
        process = subprocess.Popen(  # noqa: S603 - SSH host is explicitly configured by operator
            ["ssh",  # noqa: S607 - ssh is resolved through PATH by design
             "-T", "-o", "BatchMode=yes", host, command],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, bufsize=1,
        )
        assert process.stdin is not None and process.stdout is not None
        process.stdin.write(json.dumps(keys, separators=(",", ":")) + "\n")
        process.stdin.flush()
        process.stdin.close()
        final = consume_remote_stream(
            process.stdout, on_log=sink.on_log, on_audit=sink.on_audit,
            on_status=sink.on_status, on_heartbeat=sink.on_heartbeat,
            task_ref=task.id, process=process,
        )
        if final["outcome"] != "completed":
            _record_task_completion(project_root, task.id, final["outcome"], protocol=protocol, model=model)
            return 1
        # Fetch only the worker branch, then run the ordinary local verifier
        # gate and merge. Git-fetch failure is an operational task error.
        subprocess.run(  # noqa: S603 - internal branch ref and operator-selected SSH remote
            ["git",  # noqa: S607 - git is resolved through PATH by design
             "-C", project_root, "fetch", "--quiet", remote_repo,
             f"+refs/heads/{branch}:refs/heads/{branch}"],
            capture_output=True, text=True, check=True,
        )
        _record_task_completion(project_root, task.id, "completed", protocol=protocol, model=model)
        session_id = None
        session_manager = getattr(args, "session_manager", None)
        if session_manager:
            from snodo.infrastructure.state import read_state
            state = read_state(project_root)
            session = session_manager.get_active_session(
                getattr(args, "mode", None) or state.current_mode or protocol.initial_mode,
                project_root,
            )
            session_id = session.session_id if session else None
        result, _preserve, _branch = run_cmd._merge_on_success(
            project_root, task, 0, session_id, sink.audit_log, plan_name=plan_name,
        )
        if result != 0:
            _record_task_completion(project_root, task.id, "unmerged", protocol=protocol, model=model)
        return result
    except (OSError, subprocess.SubprocessError, RemoteStreamError, RuntimeError) as exc:
        print(f"Remote task failed on {host}: {exc}", file=sys.stderr)
        status_writer(task.id, "errored")
        _record_task_completion(project_root, task.id, "errored", protocol=protocol, model=model)
        return 1
    finally:
        if 'process' in locals() and process.poll() is None:
            process.kill()
            process.wait()
        if 'process' in locals() and process.stderr:
            process.stderr.close()


def _write_task_status(project_root: str, args, task_ref: str, status: str) -> None:
    """Write remote liveness/status through the regular local record paths."""
    from snodo.infrastructure.state import atomic_update_json

    task_dir = Path(project_root) / ".snodo" / "tasks" / task_ref
    atomic_update_json(task_dir, "state.json", lambda state: state.update({"status": status}))
    plan = getattr(args, "plan", None) or os.environ.get("SNODO_TASK_PLAN")
    if plan:
        from snodo.mcp.planner import PlannerMCP
        PlannerMCP(project_root, audit_log=getattr(args, "audit_log", None)).update_status(
            plan, task_ref, status,
        )

"""Stateless one-task worker used by the SSH transport (ADR 055)."""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Optional

import typer

from snodo.mcp.status import TASK_STATUSES

_HEARTBEAT_SECONDS = 5.0


class _Stream:
    """Serialize worker records and redact credentials from every payload."""

    def __init__(self, secrets: list[str]):
        self.secrets = [secret for secret in secrets if secret]
        self.lock = threading.Lock()

    def redact(self, value):
        if isinstance(value, str):
            for secret in self.secrets:
                value = value.replace(secret, "[REDACTED]")
            return value
        if isinstance(value, list):
            return [self.redact(item) for item in value]
        if isinstance(value, dict):
            return {key: self.redact(item) for key, item in value.items()}
        return value

    def emit(self, kind: str, **payload) -> None:
        record = self.redact({"v": 1, "kind": kind, **payload})
        encoded = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        with self.lock:
            sys.__stdout__.write(encoded + "\n")
            sys.__stdout__.flush()


class _OutputCapture:
    """Convert arbitrary task output into line-oriented log records."""

    def __init__(self, stream: _Stream, channel: str):
        self.stream = stream
        self.channel = channel
        self.buffer = ""

    def write(self, text: str) -> int:
        self.buffer += str(text)
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            self.stream.emit("log", stream=self.channel, line=line)
        return len(text)

    def flush(self) -> None:
        if self.buffer:
            self.stream.emit("log", stream=self.channel, line=self.buffer)
            self.buffer = ""

    def isatty(self) -> bool:
        return False


class _AuditStream:
    """AuditLog-compatible sink that emits events without persisting them."""

    def __init__(self, stream: _Stream):
        self.stream = stream

    def append_event(self, event_type: str, data: dict):
        self.stream.emit("audit", event_type=event_type, data=data)


def _git(project_root: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(  # noqa: S603 - fixed git executable and subcommands
        ["git", "-C", str(project_root), *args],  # noqa: S607 - git resolved from PATH by design
        capture_output=True,
        text=True,
        check=False,
    )
    if check and result.returncode:
        raise RuntimeError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def run_remote_task(
    *, project_root: str, task_id: str, spec: str,
    protocol_path: str = ".snodo/protocol.yml", model: Optional[str] = None,
    coder: Optional[str] = None, mode: Optional[str] = None,
    plan: Optional[str] = None, mock: bool = False,
) -> int:
    """Run one complete task and report only its JSONL stream on stdout."""
    try:
        credentials = json.loads(sys.stdin.readline())
    except (json.JSONDecodeError, TypeError) as exc:
        print(f"Worker expected provider keys as the first stdin JSON object: {exc}", file=sys.stderr)
        return 2
    if not isinstance(credentials, dict) or any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in credentials.items()
    ):
        print("Worker provider keys must be a JSON object of string values", file=sys.stderr)
        return 2

    stream = _Stream(list(credentials.values()))
    stdout = _OutputCapture(stream, "stdout")
    stderr = _OutputCapture(stream, "stderr")
    old_env = {key: os.environ.get(key) for key in credentials}
    os.environ.update(credentials)
    project = Path(project_root).resolve()
    branch = ""
    task_wt = None
    head_sha = ""
    outcome = "errored"
    stop_heartbeat = threading.Event()
    stream.emit("status", task_ref=task_id, status="in_progress")

    def heartbeat():
        while not stop_heartbeat.wait(_HEARTBEAT_SECONDS):
            stream.emit("heartbeat")

    ticker = threading.Thread(target=heartbeat, daemon=True)
    ticker.start()
    try:
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            from snodo.cli.commands import load_protocol
            from snodo.infrastructure.worktree import setup_for_task, task_branch_name
            from snodo.paths import derive_task_id

            root = str(project)
            if not task_id:
                task_id = derive_task_id(spec)
            proto_path = Path(protocol_path)
            if not proto_path.is_absolute():
                proto_path = project / proto_path
            protocol = load_protocol(proto_path)
            if protocol is None:
                raise RuntimeError(f"Could not load protocol: {proto_path}")
            branch = task_branch_name(task_id, spec, plan)
            task_wt = setup_for_task(root, task_id, spec, plan_name=plan, protocol=protocol)
            if not task_wt:
                raise RuntimeError("Could not create task worktree")
            branch = _git(Path(task_wt), "branch", "--show-current")
            _git(Path(task_wt), "config", "user.name", "snodo worker")
            _git(Path(task_wt), "config", "user.email", "snodo-worker@localhost")

            from snodo.engine.closure import run_to_closure
            from snodo.infrastructure.tokens import TokenIssuer
            from snodo.validators.verdict_cache import cache_for_project

            selected_mode = mode or protocol.initial_mode
            selected_model = model or ""
            if not selected_model:
                from snodo.config import ConfigManager
                selected_model = ConfigManager().get_coder_model()
            # Worker-only resources are confined to the remote clone. No session,
            # cloud hooks, status writer, or durable audit logger is constructed.
            token_issuer = TokenIssuer(store_path=project / ".snodo" / "worker-tokens.sqlite")
            try:
                from snodo.engine.loop import build_protocol_graph
                graph = build_protocol_graph(
                    protocol, project_root=root, use_mock_coder=mock,
                    model=selected_model, coder_name=coder, audit_log=_AuditStream(stream),
                    worktree_path=task_wt, token_issuer=token_issuer,
                    verdict_cache=cache_for_project(project),
                ).compile()
                result_state, closure = run_to_closure(
                    graph,
                    {"id": task_id, "spec": spec, "root_spec": spec},
                    mode=selected_mode,
                    audit_log=_AuditStream(stream),
                    max_total_fix_attempts=getattr(protocol.execution, "max_total_fix_attempts", 10),
                    max_recovery_depth=getattr(protocol.execution, "max_recovery_depth", 3),
                )
            finally:
                token_issuer.close()

            outcome = _status_for_closure(closure, result_state)
            stream.emit("status", task_ref=task_id, status=outcome)
            head_sha = _git(Path(task_wt), "rev-parse", "HEAD")
    except Exception as exc:  # failures still produce a complete worker record
        stream.emit("log", stream="snodo", line=f"Worker task failed: {exc}")
        outcome = "errored"
        try:
            repo_for_head = Path(task_wt) if task_wt else project
            if not branch:
                branch = _git(repo_for_head, "branch", "--show-current", check=False)
            head_sha = _git(repo_for_head, "rev-parse", "HEAD", check=False)
        except OSError as git_error:
            stream.emit("log", stream="snodo", line=f"Could not read worker HEAD: {git_error}")
    finally:
        stdout.flush()
        stderr.flush()
        stop_heartbeat.set()
        ticker.join(timeout=1)
        for key, value in old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    if outcome not in TASK_STATUSES:
        outcome = "errored"
    stream.emit("final", outcome=outcome, branch=branch, head_sha=head_sha)
    return 0


def _status_for_closure(tree, final_state: dict) -> str:
    if tree is not None and tree.outcome == "resolved":
        return "completed"
    payload = ((final_state or {}).get("metadata") or {}).get("halt_payload") or {}
    decision = payload.get("final_decision") or payload.get("halt_type")
    if decision in {"internal_error", "validator_error", "environment_error"}:
        return "errored"
    return "blocked"


def register(app: typer.Typer) -> None:
    """Register the internal SSH worker command (not part of public help)."""

    @app.command("_worker", hidden=True)
    def worker(
        project_root: str = typer.Option(..., "--project-root"),
        task_id: str = typer.Option(..., "--task-id"),
        spec: str = typer.Option(..., "--spec"),
        protocol: str = typer.Option(".snodo/protocol.yml", "--protocol"),
        model: Optional[str] = typer.Option(None, "--model"),
        coder: Optional[str] = typer.Option(None, "--coder"),
        mode: Optional[str] = typer.Option(None, "--mode"),
        plan: Optional[str] = typer.Option(None, "--plan"),
        mock: bool = typer.Option(False, "--mock"),
    ):
        """Internal stateless task worker; invoked by the local SSH dispatcher."""
        return run_remote_task(
            project_root=project_root, task_id=task_id, spec=spec,
            protocol_path=protocol, model=model, coder=coder,
            mode=mode, plan=plan, mock=mock,
        )

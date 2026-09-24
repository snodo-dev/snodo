"""Read a remote worker's JSON-lines stream on the local machine (ADR 055)."""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, Optional

from snodo.mcp.status import TASK_STATUSES

_logger = logging.getLogger(__name__)

HEARTBEAT_TIMEOUT = 60.0
_LOG_STREAMS = {"stdout", "stderr", "snodo"}
_KINDS = {"log", "audit", "status", "heartbeat", "final"}


class RemoteStreamError(RuntimeError):
    """A remote worker stream violated ADR 055 or failed to complete."""


class LocalRemoteStreamSink:
    """Route validated records into the task's existing local write paths.

    ``status_writer`` is the caller's normal PlannerMCP/task-status writer;
    it is intentionally injected so remote records cannot bypass its
    vocabulary checks and liveness transition behavior.
    """

    def __init__(self, project_root: str, job_id: str, audit_log: Any, status_writer: Callable[[str, str], None]):
        self.project_root = str(project_root)
        self.log_path = Path(project_root) / ".snodo" / "jobs" / job_id / "stdout.log"
        self.audit_log = audit_log
        self.status_writer = status_writer

    def on_log(self, stream: str, line: str) -> None:
        """Append worker output to the stdout log tailed by ``snodo logs``."""
        del stream  # snodo logs follows the task's unified output stream
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as log:
            log.write(line)
            if not line.endswith("\n"):
                log.write("\n")
            log.flush()

    def on_audit(self, event_type: str, data: dict) -> None:
        """Append through the local hash-chained audit logger."""
        self.audit_log.append_event(event_type, data)

    def on_status(self, task_ref: str, status: str) -> None:
        """Use the regular task status writer."""
        self.status_writer(task_ref, status)

    def on_heartbeat(self) -> None:
        """Refresh local liveness using the same transition hook as task writes."""
        from snodo.infrastructure import cloud_liveness

        cloud_liveness.note_transition(self.project_root)


def consume_remote_stream(
    lines: Iterable[str],
    *,
    on_log: Callable[[str, str], None],
    on_audit: Callable[[str, dict], None],
    on_status: Callable[[str, str], None],
    on_heartbeat: Callable[[], None],
    task_ref: Optional[str] = None,
    process: Any = None,
    timeout: float = HEARTBEAT_TIMEOUT,
    clock: Any = time,
) -> dict:
    """Consume worker records and return its sole final record.

    ``lines`` may be a process stdout iterator, an open file, or any iterable
    of newline-delimited strings. Iteration runs in a daemon producer so a
    blocked process/file read is bounded by the heartbeat timeout. Callbacks
    are the local write paths: job log, ``AuditLog.append_event``, task/plan
    status update, and local liveness respectively.

    A supplied *process* must expose ``wait()``; a nonzero SSH exit invalidates
    even an otherwise valid final record.
    """
    records: queue.Queue = queue.Queue()
    sentinel = object()

    def fail(message: str, cause: Optional[BaseException] = None) -> None:
        if task_ref is not None:
            try:
                on_status(task_ref, "errored")
            except Exception:
                _logger.exception("Could not mark task %s errored after remote stream failure", task_ref)
        error = RemoteStreamError(message)
        if cause is not None:
            raise error from cause
        raise error

    def produce() -> None:
        try:
            for line in lines:
                records.put(("line", line))
        except BaseException as exc:  # propagate iterator/read errors locally
            records.put(("error", exc))
        finally:
            records.put(("eof", sentinel))

    threading.Thread(target=produce, daemon=True).start()
    final = None
    while True:
        try:
            kind, value = records.get(timeout=timeout)
        except queue.Empty:
            fail(
                f"remote stream timed out: no line received for {timeout:g} seconds"
            )

        if kind == "error":
            fail(f"remote stream read failed: {value}", value)
        if kind == "eof":
            break
        if final is not None:
            fail("remote stream malformed: record received after final")

        line = value
        if not isinstance(line, str):
            fail("remote stream malformed: line is not text")
        try:
            record = json.loads(line)
        except (json.JSONDecodeError, TypeError) as exc:
            fail(f"remote stream malformed JSON line: {exc}", exc)
        if not isinstance(record, dict):
            fail("remote stream malformed: record must be an object")
        if record.get("v") != 1 or isinstance(record.get("v"), bool):
            fail(f"remote stream has unknown v: {record.get('v')!r}")
        record_kind = record.get("kind")
        if not isinstance(record_kind, str) or record_kind not in _KINDS:
            fail(f"remote stream malformed: unknown kind {record_kind!r}")

        payload = {key: item for key, item in record.items() if key not in {"v", "kind"}}
        if record_kind == "log":
            stream, text = payload.get("stream"), payload.get("line")
            if not isinstance(stream, str) or stream not in _LOG_STREAMS or not isinstance(text, str):
                fail("remote stream malformed log record")
            on_log(stream, text)
        elif record_kind == "audit":
            event_type, data = payload.get("event_type"), payload.get("data")
            if not isinstance(event_type, str) or not event_type or not isinstance(data, dict):
                fail("remote stream malformed audit record")
            on_audit(event_type, data)
        elif record_kind == "status":
            ref, status = payload.get("task_ref"), payload.get("status")
            if not isinstance(ref, str) or not ref or not isinstance(status, str):
                fail("remote stream malformed status record")
            if status not in TASK_STATUSES:
                fail(f"remote stream rejected unknown task status {status!r}")
            if task_ref is not None and ref != task_ref:
                fail(f"remote stream status task_ref {ref!r} does not match {task_ref!r}")
            on_status(ref, status)
        elif record_kind == "heartbeat":
            on_heartbeat()
        elif record_kind == "final":
            outcome = payload.get("outcome")
            if not isinstance(outcome, str) or outcome not in TASK_STATUSES:
                fail(f"remote stream rejected unknown final outcome {outcome!r}")
            if not isinstance(payload.get("branch"), str) or not isinstance(payload.get("head_sha"), str):
                fail("remote stream malformed final record")
            final = payload

    if final is None:
        fail("remote stream ended without final record")
    if process is not None:
        exit_code = process.wait()
        if exit_code != 0:
            fail(f"remote SSH process exited non-zero ({exit_code})")
    if task_ref is not None:
        on_status(task_ref, final["outcome"])
    return final

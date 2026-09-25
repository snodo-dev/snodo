"""Local JSON-lines reader for remote task workers (ADR 055)."""

import json
import os
import subprocess
import sys
import threading
from collections import deque

import pytest

from snodo.cli.commands.remote_stream import (
    LocalRemoteStreamSink,
    RemoteStreamError,
    consume_remote_stream,
)
from snodo.infrastructure.audit import AuditLog


def wire(kind, **payload):
    return json.dumps({"v": 1, "kind": kind, **payload})


def consume(lines, audit_log=None, **kwargs):
    received = {"logs": [], "statuses": [], "heartbeats": 0}
    result = consume_remote_stream(
        lines,
        on_log=lambda stream, line: received["logs"].append((stream, line)),
        on_audit=(audit_log.append_event if audit_log else lambda *_: None),
        on_status=lambda ref, status: received["statuses"].append((ref, status)),
        on_heartbeat=lambda: received.__setitem__("heartbeats", received["heartbeats"] + 1),
        task_ref="p:task",
        **kwargs,
    )
    return result, received


def test_happy_path_dispatches_records_and_preserves_audit_chain(tmp_path):
    audit = AuditLog(str(tmp_path / "audit.log"), project_id="test")
    result, received = consume([
        wire("log", stream="stdout", line="working"),
        wire("audit", event_type="remote_step", data={"step": 1}),
        wire("status", task_ref="p:task", status="in_progress"),
        wire("heartbeat"),
        wire("final", outcome="completed", branch="task/one", head_sha="abc123"),
    ], audit)

    assert result["outcome"] == "completed"
    assert received == {
        "logs": [("stdout", "working")],
        "statuses": [("p:task", "in_progress"), ("p:task", "completed")],
        "heartbeats": 1,
    }
    assert audit.verify_chain()
    assert audit.get_history("remote_step")[0].data == {"step": 1}


def test_local_sink_appends_to_job_log_and_normal_status_path(tmp_path):
    root = tmp_path / "project"
    job_id = "j_remote"
    job_dir = root / ".snodo" / "jobs" / job_id
    job_dir.mkdir(parents=True)
    written_statuses = []
    audit = AuditLog(str(root / ".snodo" / "audit.log"), project_id="test")
    sink = LocalRemoteStreamSink(
        str(root), job_id, audit,
        lambda task_ref, status: written_statuses.append((task_ref, status)),
    )
    sink.on_log("stderr", "validator output")
    sink.on_audit("remote_event", {"ok": True})
    sink.on_status("p:task", "in_progress")

    assert (job_dir / "stdout.log").read_text() == "validator output\n"
    assert written_statuses == [("p:task", "in_progress")]
    assert audit.verify_chain()


@pytest.mark.parametrize(
    ("lines", "message"),
    [
        ([], "ended without final"),
        (["not-json"], "malformed JSON"),
        ([json.dumps({"v": 2, "kind": "heartbeat"})], "unknown v"),
        ([wire("final", outcome="completed", branch="b", head_sha="s"), wire("heartbeat")], "after final"),
        ([wire("status", task_ref="p:task", status="made_up")], "unknown task status"),
    ],
)
def test_stream_failure_rules(lines, message):
    with pytest.raises(RemoteStreamError, match=message):
        consume(lines)


def test_stream_times_out_when_no_line_arrives():
    blocked = threading.Event()

    def silent_source():
        blocked.wait()
        yield wire("heartbeat")

    with pytest.raises(RemoteStreamError, match="no line received for 0.01 seconds"):
        consume(silent_source(), timeout=0.01)
    blocked.set()


def test_nonzero_ssh_exit_invalidates_final_record():
    class FailedProcess:
        def wait(self):
            return 255

    with pytest.raises(RemoteStreamError, match=r"exited non-zero \(255\)"):
        consume([wire("final", outcome="completed", branch="b", head_sha="s")], process=FailedProcess())


@pytest.mark.parametrize("emit_record", [False, True])
def test_fake_ssh_stderr_and_exit_are_reported_before_or_after_records(tmp_path, emit_record):
    fake_ssh = tmp_path / "ssh"
    output = wire("final", outcome="completed", branch="b", head_sha="s") if emit_record else ""
    fake_ssh.write_text(
        f"#!{sys.executable}\n"
        "import sys\n"
        "print('remote worker diagnostic', file=sys.stderr, flush=True)\n"
        f"sys.stdout.write({output!r} + ('\\n' if {bool(output)!r} else ''))\n"
        "sys.stdout.flush()\n"
        "sys.exit(23)\n"
    )
    fake_ssh.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{tmp_path}{os.pathsep}{env['PATH']}"
    process = subprocess.Popen(
        ["ssh"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
    )
    assert process.stdout is not None and process.stderr is not None
    lines = deque(maxlen=20)
    logged = []
    done = threading.Event()

    def capture():
        try:
            for line in process.stderr:
                text = line.rstrip("\r\n")
                lines.append(text)
                logged.append(f"[remote stderr] {text}")
        finally:
            done.set()

    threading.Thread(target=capture, daemon=True).start()
    with pytest.raises(RemoteStreamError) as exc:
        consume(process.stdout, process=process, stderr_tail=lambda: list(lines), stderr_done=done)
    assert "SSH exit code: 23" in str(exc.value)
    assert "remote worker diagnostic" in str(exc.value)
    assert logged == ["[remote stderr] remote worker diagnostic"]


def test_final_branch_sync_runs_before_completed_status():
    events = []
    final = wire("final", outcome="completed", branch="task/one", head_sha="abc")

    consume_remote_stream(
        [final],
        on_log=lambda *_: None,
        on_audit=lambda *_: None,
        on_status=lambda _ref, status: events.append(status),
        on_heartbeat=lambda: None,
        task_ref="p:task",
        on_final=lambda record: events.append(record["head_sha"]),
    )

    assert events == ["abc", "completed"]


def test_final_branch_sync_failure_marks_task_errored():
    statuses = []
    with pytest.raises(RemoteStreamError, match="branch sync failed"):
        consume_remote_stream(
            [wire("final", outcome="completed", branch="task/one", head_sha="abc")],
            on_log=lambda *_: None,
            on_audit=lambda *_: None,
            on_status=lambda _ref, status: statuses.append(status),
            on_heartbeat=lambda: None,
            task_ref="p:task",
            on_final=lambda _record: (_ for _ in ()).throw(RuntimeError("head mismatch")),
        )
    assert statuses == ["errored"]

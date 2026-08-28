"""Tests for append-only audit log with hash chain."""

import asyncio
import pytest
import tempfile
import json
import threading
import time
from pathlib import Path

from snodo.infrastructure.audit import (
    AuditLog, AuditEvent, get_audit_log, log_event
)
from snodo.core.interfaces import AuditError


@pytest.fixture
def temp_audit_log():
    """Create a temporary audit log for testing."""
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.log') as f:
        log_path = f.name
    
    audit_log = AuditLog(log_path)
    yield audit_log
    
    # Cleanup
    Path(log_path).unlink(missing_ok=True)


# ========== APPEND EVENT TESTS ==========

def test_append_single_event(temp_audit_log):
    """Test appending a single event."""
    event = temp_audit_log.append_event("test_event", {"key": "value"})
    
    assert event.sequence == 0
    assert event.event_type == "test_event"
    assert event.data == {"key": "value"}
    assert event.previous_hash == "0" * 64
    assert len(event.event_hash) == 64


def test_append_multiple_events(temp_audit_log):
    """Test appending multiple events forms a chain."""
    event1 = temp_audit_log.append_event("event1", {"data": 1})
    event2 = temp_audit_log.append_event("event2", {"data": 2})
    event3 = temp_audit_log.append_event("event3", {"data": 3})
    
    assert event1.sequence == 0
    assert event2.sequence == 1
    assert event3.sequence == 2
    
    # Verify chain links
    assert event2.previous_hash == event1.event_hash
    assert event3.previous_hash == event2.event_hash


def test_event_timestamps(temp_audit_log):
    """Test that events have timestamps."""
    event = temp_audit_log.append_event("test", {})
    
    assert "timestamp" in event.__dict__
    assert event.timestamp.endswith("Z") or "+" in event.timestamp  # ISO format


# ========== HASH CHAIN TESTS ==========

def test_verify_chain_empty_log(temp_audit_log):
    """Test verifying empty log returns True."""
    assert temp_audit_log.verify_chain() is True


def test_verify_chain_valid(temp_audit_log):
    """Test verifying valid chain returns True."""
    temp_audit_log.append_event("e1", {"a": 1})
    temp_audit_log.append_event("e2", {"b": 2})
    temp_audit_log.append_event("e3", {"c": 3})
    
    assert temp_audit_log.verify_chain() is True


def test_verify_chain_detects_tampered_data(temp_audit_log):
    """Test that tampering with event data breaks verification."""
    temp_audit_log.append_event("e1", {"original": "data"})
    temp_audit_log.append_event("e2", {"more": "data"})
    
    # Tamper with first event's data
    temp_audit_log.events[0].data["original"] = "tampered"
    
    assert temp_audit_log.verify_chain() is False


def test_verify_chain_detects_tampered_hash(temp_audit_log):
    """Test that tampering with hash breaks verification."""
    temp_audit_log.append_event("e1", {"data": 1})
    temp_audit_log.append_event("e2", {"data": 2})
    
    # Tamper with hash
    temp_audit_log.events[0].event_hash = "0" * 64
    
    assert temp_audit_log.verify_chain() is False


def test_verify_chain_detects_sequence_mismatch(temp_audit_log):
    """Test that sequence number mismatch is detected."""
    temp_audit_log.append_event("e1", {})
    temp_audit_log.append_event("e2", {})
    
    # Tamper with sequence
    temp_audit_log.events[1].sequence = 999
    
    assert temp_audit_log.verify_chain() is False


def test_verify_chain_detects_broken_link(temp_audit_log):
    """Test that broken chain link is detected."""
    temp_audit_log.append_event("e1", {})
    temp_audit_log.append_event("e2", {})
    temp_audit_log.append_event("e3", {})
    
    # Break the chain by changing previous_hash
    temp_audit_log.events[2].previous_hash = "0" * 64
    
    assert temp_audit_log.verify_chain() is False


# ========== HISTORY TESTS ==========

def test_get_history_all(temp_audit_log):
    """Test getting all event history."""
    temp_audit_log.append_event("type1", {"a": 1})
    temp_audit_log.append_event("type2", {"b": 2})
    temp_audit_log.append_event("type1", {"c": 3})
    
    history = temp_audit_log.get_history()
    
    assert len(history) == 3
    assert all(isinstance(e, AuditEvent) for e in history)


def test_get_history_filtered(temp_audit_log):
    """Test filtering history by event type."""
    temp_audit_log.append_event("task_created", {"task": "A"})
    temp_audit_log.append_event("task_validated", {"task": "A"})
    temp_audit_log.append_event("task_created", {"task": "B"})
    temp_audit_log.append_event("task_validated", {"task": "B"})
    
    created_events = temp_audit_log.get_history(event_type="task_created")
    validated_events = temp_audit_log.get_history(event_type="task_validated")
    
    assert len(created_events) == 2
    assert len(validated_events) == 2
    assert all(e.event_type == "task_created" for e in created_events)


def test_get_history_returns_copy(temp_audit_log):
    """Test that get_history returns a copy, not reference."""
    temp_audit_log.append_event("e1", {})
    
    history = temp_audit_log.get_history()
    history.append("fake_event")
    
    # Original should be unchanged
    assert len(temp_audit_log.get_history()) == 1


# ========== PERSISTENCE TESTS ==========

def test_events_persisted_to_disk(temp_audit_log):
    """Test that events are written to disk."""
    temp_audit_log.append_event("test", {"data": "value"})
    
    # Check file exists and has content
    assert Path(temp_audit_log.log_path).exists()
    
    with open(temp_audit_log.log_path, 'r') as f:
        lines = f.readlines()
    
    assert len(lines) == 1
    event_dict = json.loads(lines[0])
    assert event_dict["event_type"] == "test"


def test_load_existing_log(temp_audit_log):
    """Test loading existing log from disk."""
    # Create events
    temp_audit_log.append_event("e1", {"data": 1})
    temp_audit_log.append_event("e2", {"data": 2})
    
    # Create new log instance pointing to same file
    new_log = AuditLog(temp_audit_log.log_path)
    
    assert len(new_log.events) == 2
    assert new_log.events[0].event_type == "e1"
    assert new_log.events[1].event_type == "e2"
    assert new_log.verify_chain() is True


def test_jsonl_format(temp_audit_log):
    """Test that log uses JSONL (JSON Lines) format."""
    temp_audit_log.append_event("e1", {})
    temp_audit_log.append_event("e2", {})
    
    with open(temp_audit_log.log_path, 'r') as f:
        lines = f.readlines()
    
    # Each line should be valid JSON
    for line in lines:
        json.loads(line)  # Should not raise


# ========== LOAD FAILURE TESTS (issue #10) ==========

def _write_events(log_path, n):
    """Write n valid chained events to disk and return the AuditLog."""
    log = AuditLog(str(log_path))
    for i in range(n):
        log.append_event(f"e{i}", {"i": i})
    return log


def _corrupt_line(log_path, line_no, new_line):
    """Replace line `line_no` (1-indexed) of the log file with `new_line`."""
    lines = log_path.read_text().splitlines()
    lines[line_no - 1] = new_line
    log_path.write_text("\n".join(lines) + "\n")


def test_load_raises_on_malformed_line(tmp_path):
    """A malformed line raises AuditError naming the line and path."""
    log_path = tmp_path / "audit.log"
    _write_events(log_path, 5)
    _corrupt_line(log_path, 2, "{not valid json")

    with pytest.raises(AuditError, match="line 2"):
        AuditLog(str(log_path))


def test_load_raises_on_hash_mismatch(tmp_path):
    """A tampered event hash raises AuditError naming the line."""
    log_path = tmp_path / "audit.log"
    _write_events(log_path, 5)
    lines = log_path.read_text().splitlines()
    event = json.loads(lines[1])
    event["data"] = {"tampered": True}
    lines[1] = json.dumps(event)
    log_path.write_text("\n".join(lines) + "\n")

    with pytest.raises(AuditError, match="line 2"):
        AuditLog(str(log_path))


def test_load_raises_on_sequence_discontinuity(tmp_path):
    """A sequence gap raises AuditError naming the line."""
    log_path = tmp_path / "audit.log"
    _write_events(log_path, 5)
    lines = log_path.read_text().splitlines()
    event = json.loads(lines[2])
    event["sequence"] = 99
    lines[2] = json.dumps(event)
    log_path.write_text("\n".join(lines) + "\n")

    with pytest.raises(AuditError, match="line 3"):
        AuditLog(str(log_path))


def test_append_refused_after_failed_load(tmp_path):
    """After a failed load, append_event raises rather than forking the chain."""
    log_path = tmp_path / "audit.log"
    _write_events(log_path, 5)
    _corrupt_line(log_path, 2, "{not valid json")

    with pytest.raises(AuditError):
        AuditLog(str(log_path))

    # Simulate the object that would exist after a truncated load: a log whose
    # in-memory events are a strict prefix of the file on disk.
    truncated = AuditLog.__new__(AuditLog)
    truncated.log_path = log_path
    truncated._project_id = ""
    truncated._lock = threading.Lock()
    truncated._load_ok = False
    truncated.events = []

    with pytest.raises(AuditError, match="did not load cleanly"):
        truncated.append_event("forked", {})


def test_verify_chain_detects_file_beyond_memory(tmp_path):
    """verify_chain returns False when disk has events beyond those loaded."""
    log_path = tmp_path / "audit.log"
    _write_events(log_path, 5)

    # Simulate a truncated load: memory holds only the first 2 events while the
    # file still contains all 5.
    truncated = AuditLog.__new__(AuditLog)
    truncated.log_path = log_path
    truncated._project_id = ""
    truncated._lock = threading.Lock()
    truncated._load_ok = True
    truncated.events = [AuditLog(str(log_path)).events[i] for i in range(2)]

    assert truncated.verify_chain() is False


def test_verify_chain_false_after_failed_load(tmp_path):
    """verify_chain returns False (not success) when load failed."""
    log_path = tmp_path / "audit.log"
    _write_events(log_path, 5)
    _corrupt_line(log_path, 2, "{not valid json")

    with pytest.raises(AuditError):
        AuditLog(str(log_path))

    truncated = AuditLog.__new__(AuditLog)
    truncated.log_path = log_path
    truncated._project_id = ""
    truncated._lock = threading.Lock()
    truncated._load_ok = False
    truncated.events = []

    assert truncated.verify_chain() is False


def test_valid_log_still_loads_verifies_appends(tmp_path):
    """Regression guard: a valid log loads, verifies, and appends unchanged."""
    log_path = tmp_path / "audit.log"
    _write_events(log_path, 5)

    resumed = AuditLog(str(log_path))
    assert len(resumed.events) == 5
    assert resumed.verify_chain() is True

    last_hash = resumed.events[-1].event_hash
    resumed.append_event("e5", {"i": 5})
    assert len(resumed.events) == 6
    assert resumed.events[5].previous_hash == last_hash
    assert resumed.verify_chain() is True


# ========== HASH COMPUTATION TESTS ==========

def test_hash_deterministic(temp_audit_log):
    """Test that same input produces same hash."""
    hash1 = temp_audit_log._compute_hash(
        0, "2024-01-01", "test", {"a": 1}, "0" * 64
    )
    hash2 = temp_audit_log._compute_hash(
        0, "2024-01-01", "test", {"a": 1}, "0" * 64
    )
    
    assert hash1 == hash2


def test_hash_changes_with_data(temp_audit_log):
    """Test that different data produces different hash."""
    hash1 = temp_audit_log._compute_hash(
        0, "2024-01-01", "test", {"a": 1}, "0" * 64
    )
    hash2 = temp_audit_log._compute_hash(
        0, "2024-01-01", "test", {"a": 2}, "0" * 64
    )
    
    assert hash1 != hash2


# ========== GLOBAL INSTANCE TESTS ==========

def test_get_audit_log_singleton():
    """Test that get_audit_log returns singleton."""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = Path(tmpdir) / "test.log"
        
        log1 = get_audit_log(str(log_path))
        log2 = get_audit_log(str(log_path))
        
        assert log1 is log2


def test_audit_log_resolves_to_project_root_despite_snodo_home(tmp_path, monkeypatch):
    """The audit log is a property of the PROJECT, not the user.

    SNODO_HOME is the ~/.snodo equivalent — config, sessions, memory all live
    under it. The project audit log must resolve against the project root even
    when SNODO_HOME points elsewhere; a home-scoped default would append every
    project's hash-chained log to one shared file and corrupt each other's
    continuity (Fixes #111).
    """
    from snodo.infrastructure.audit import reset_global_audit_log

    project_root = tmp_path / "project"
    snodo_home = tmp_path / "snodo_home"
    project_root.mkdir()
    snodo_home.mkdir()

    monkeypatch.setenv("SNODO_HOME", str(snodo_home))
    # The autouse isolate_snodo_home fixture sets SNODO_AUDIT_LOG to keep unit
    # tests off the suite repo; this canary tests the DEFAULT resolution, so
    # the override must be cleared.
    monkeypatch.delenv("SNODO_AUDIT_LOG", raising=False)
    monkeypatch.chdir(project_root)
    reset_global_audit_log()
    try:
        log = get_audit_log()
        assert Path(log.log_path).resolve() == (project_root / ".snodo" / "audit.log").resolve(), (
            f"audit log resolved to {log.log_path} — the project audit log must "
            "resolve against the project root, not SNODO_HOME"
        )
    finally:
        reset_global_audit_log()


def test_log_event_convenience_function():
    """Test log_event convenience function."""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = Path(tmpdir) / "test.log"
        
        # Reset global instance
        import snodo.infrastructure.audit as audit_module
        audit_module._global_audit_log = AuditLog(str(log_path))
        
        event = log_event("test_type", {"test": "data"})
        
        assert event.event_type == "test_type"
        assert event.data == {"test": "data"}


# ========== EDGE CASES ==========

def test_empty_data_dict(temp_audit_log):
    """Test appending event with empty data."""
    event = temp_audit_log.append_event("empty", {})
    
    assert event.data == {}
    assert temp_audit_log.verify_chain() is True


def test_complex_nested_data(temp_audit_log):
    """Test appending event with complex nested data."""
    complex_data = {
        "nested": {
            "list": [1, 2, 3],
            "dict": {"a": "b"}
        },
        "array": [{"x": 1}, {"y": 2}]
    }
    
    event = temp_audit_log.append_event("complex", complex_data)
    
    assert event.data == complex_data
    assert temp_audit_log.verify_chain() is True


def test_first_event_genesis_hash(temp_audit_log):
    """Test that first event uses genesis hash (all zeros)."""
    event = temp_audit_log.append_event("genesis", {})

    assert event.previous_hash == "0" * 64
    assert event.sequence == 0


# ========== TASK 7.1: THREAD SAFETY TESTS ==========

def test_audit_log_has_lock(temp_audit_log):
    """AuditLog has a threading lock."""
    import threading
    assert isinstance(temp_audit_log._lock, type(threading.Lock()))


def test_concurrent_appends_via_asyncio(temp_audit_log):
    """Concurrent appends via asyncio.gather maintain chain integrity."""

    async def append_one(idx):
        temp_audit_log.append_event("concurrent", {"idx": idx})

    async def run_all():
        tasks = [append_one(i) for i in range(10)]
        await asyncio.gather(*tasks)

    asyncio.run(run_all())

    assert len(temp_audit_log.events) == 10
    assert temp_audit_log.verify_chain() is True
    # Verify all 10 are present
    idxs = sorted(e.data["idx"] for e in temp_audit_log.events)
    assert idxs == list(range(10))


# ========== TASK 7.1: DISK ERROR HANDLING TESTS ==========

def test_disk_failure_retries_once_then_raises(temp_audit_log):
    """On disk failure, retries once then raises AuditError."""
    call_count = 0

    def failing_disk(event):
        nonlocal call_count
        call_count += 1
        raise OSError("disk full")

    temp_audit_log._append_to_disk = failing_disk

    with pytest.raises(AuditError, match="disk full"):
        temp_audit_log.append_event("fail_test", {"key": "val"})

    # _append_to_disk called twice: initial + retry
    assert call_count == 2
    # Event NOT in memory (append_event rolls back on failure)
    assert len(temp_audit_log.events) == 0


def test_disk_failure_raises_audit_error(temp_audit_log):
    """Disk failure raises AuditError, no silent drop."""
    temp_audit_log._append_to_disk = lambda e: (_ for _ in ()).throw(OSError("boom"))

    with pytest.raises(AuditError, match="boom"):
        temp_audit_log.append_event("err", {})


def test_disk_retry_succeeds_second_time(temp_audit_log):
    """If retry succeeds, no error logged."""
    call_count = 0
    original = temp_audit_log._append_to_disk

    def fail_then_succeed(event):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise OSError("transient")
        original(event)

    temp_audit_log._append_to_disk = fail_then_succeed

    event = temp_audit_log.append_event("retry_ok", {"v": 1})

    assert event is not None
    assert call_count == 2  # First failed, second succeeded
    # Verify file was written
    assert Path(temp_audit_log.log_path).exists()


# ========== TASK 7.1: SESSION RESUME TEST ==========

def test_session_resume_extends_chain(temp_audit_log):
    """On session resume, new events extend existing chain."""
    temp_audit_log.append_event("e1", {"a": 1})
    temp_audit_log.append_event("e2", {"b": 2})
    last_hash = temp_audit_log.events[-1].event_hash

    # Resume: new instance from same file
    resumed = AuditLog(str(temp_audit_log.log_path))
    resumed.append_event("e3", {"c": 3})

    assert len(resumed.events) == 3
    assert resumed.events[2].previous_hash == last_hash
    assert resumed.verify_chain() is True


# ========== CONCURRENT PROCESS APPEND (Fixes #114) ==========

def test_concurrent_process_appends_keep_chain_valid(tmp_path):
    """Two processes appending concurrently to one log must not corrupt the
    hash chain (Fixes #114).

    Against current main this must FAIL: append_event derives the next
    sequence from process-local memory, so two processes both write "their"
    next sequence and the chain breaks. After the fix the append takes an
    exclusive file lock and re-reads the last line for the true sequence.
    """
    import subprocess
    import sys

    log_path = tmp_path / "audit.log"
    barrier = tmp_path / "go"
    worker = (
        "import sys, time\n"
        "from pathlib import Path\n"
        "from snodo.infrastructure.audit import AuditLog\n"
        "log = AuditLog(sys.argv[1])\n"
        "while not Path(sys.argv[3]).exists():\n"
        "    time.sleep(0.001)\n"
        "for i in range(50):\n"
        "    log.append_event('concurrent', {'worker': sys.argv[2], 'i': i})\n"
    )
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", worker, str(log_path), str(w), str(barrier)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for w in range(2)
    ]
    # Both workers load the (empty) log, then release them together so their
    # appends genuinely interleave.
    time.sleep(0.5)
    barrier.touch()
    for p in procs:
        assert p.wait(timeout=60) == 0

    loaded = AuditLog(str(log_path))
    assert len(loaded.events) == 100
    assert loaded.verify_chain() is True


def test_append_fails_loudly_when_lock_cannot_be_acquired(tmp_path, monkeypatch):
    """When the exclusive file lock cannot be acquired, append_event fails
    loudly with AuditError — it never silently proceeds (Fixes #114)."""
    import snodo.infrastructure.audit as audit_mod

    log_path = tmp_path / "audit.log"
    audit = AuditLog(str(log_path))
    audit.append_event("e1", {"a": 1})

    # Simulate another process holding the lock forever.
    f = open(log_path, "a")
    audit_mod.fcntl.flock(f.fileno(), audit_mod.fcntl.LOCK_EX)

    monkeypatch.setattr(audit_mod, "_LOCK_TIMEOUT", 0.2)

    with pytest.raises(AuditError, match="Could not acquire the exclusive lock"):
        audit.append_event("e2", {"b": 2})

    f.close()

    # Nothing was appended; the chain is intact.
    loaded = AuditLog(str(log_path))
    assert len(loaded.events) == 1
    assert loaded.verify_chain() is True


def test_audit_log_project_stamping(tmpdir):
    """Verify that AuditLog stamps project_id on every event and verify_chain passes."""
    from snodo.infrastructure.audit import AuditLog
    
    log_path = Path(tmpdir) / "audit.log"
    # Construct with specific project_id
    audit = AuditLog(str(log_path), project_id="my-test-project-123")
    
    # Append multiple events
    audit.append_event("event1", {"data": 1})
    audit.append_event("event2", {"data": 2})
    
    # 1. verify_chain() returns True
    assert audit.verify_chain() is True
    
    # 2. Assert events in memory have project_id
    assert len(audit.events) == 2
    assert audit.events[0].project_id == "my-test-project-123"
    assert audit.events[1].project_id == "my-test-project-123"
    
    # 3. Assert events on disk have project_id
    resumed = AuditLog(str(log_path))
    assert len(resumed.events) == 2
    assert resumed.events[0].project_id == "my-test-project-123"
    assert resumed.events[1].project_id == "my-test-project-123"
    assert resumed.verify_chain() is True


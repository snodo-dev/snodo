"""Tests for append-only audit log with hash chain."""

import asyncio
import pytest
import tempfile
import json
from pathlib import Path

from snodo.infrastructure.audit import (
    AuditLog, AuditEvent, get_audit_log, log_event
)


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

def test_disk_failure_retries_once(temp_audit_log):
    """On disk failure, retries once then warns to stderr."""
    call_count = 0
    original = temp_audit_log._append_to_disk

    def failing_disk(event):
        nonlocal call_count
        call_count += 1
        raise OSError("disk full")

    temp_audit_log._append_to_disk = failing_disk

    # Should not raise
    event = temp_audit_log.append_event("fail_test", {"key": "val"})

    assert event is not None
    assert event.event_type == "fail_test"
    # _append_to_disk called twice: initial + retry
    assert call_count == 2
    # Event still in memory
    assert len(temp_audit_log.events) == 1


def test_disk_failure_warns_stderr(temp_audit_log, capsys):
    """Disk failure warning goes to stderr."""
    temp_audit_log._append_to_disk = lambda e: (_ for _ in ()).throw(OSError("boom"))

    temp_audit_log.append_event("err", {})

    captured = capsys.readouterr()
    assert "AUDIT WARNING" in captured.err
    assert "boom" in captured.err


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


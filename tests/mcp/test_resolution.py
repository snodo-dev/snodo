"""Characterization tests for snodo/mcp/resolution.py (0% → ~100%).

Pins apply_resolution: invalid input, session validation, update path,
audit logging, and FileNotFoundError propagation.
"""

import pytest
from unittest.mock import patch, MagicMock

from snodo.mcp.resolution import apply_resolution


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

def test_invalid_resolution_raises():
    with pytest.raises(ValueError, match="Resolution must be 'proceed' or 'halt'"):
        apply_resolution("t1", "sess-1", "maybe", "justification")


def test_empty_resolution_raises():
    with pytest.raises(ValueError):
        apply_resolution("t1", "sess-1", "", "justification")


# ---------------------------------------------------------------------------
# Successful resolution paths
# ---------------------------------------------------------------------------

def _patched_sm():
    """Return (patch context manager, mock manager).

    SessionManager is imported lazily inside apply_resolution, so we patch at
    the infrastructure module level where it actually lives.
    """
    mock_mgr = MagicMock()
    return patch("snodo.infrastructure.session.SessionManager", return_value=mock_mgr), mock_mgr


def test_proceed_returns_correct_shape():
    ctx, mgr = _patched_sm()
    with ctx:
        result = apply_resolution("t1", "sess-1", "proceed", "looks good")
    assert result["status"] == "resolved"
    assert result["resolution"] == "proceed"
    assert result["session_id"] == "sess-1"
    assert result["task_id"] == "t1"


def test_halt_returns_correct_shape():
    ctx, mgr = _patched_sm()
    with ctx:
        result = apply_resolution("t2", "sess-2", "halt", "too risky", resolved_by="human")
    assert result["resolution"] == "halt"
    assert result["task_id"] == "t2"


def test_load_session_called_for_validation():
    ctx, mgr = _patched_sm()
    with ctx:
        apply_resolution("t1", "sess-1", "proceed", "ok")
    mgr.load_session.assert_called_once_with("sess-1")


def test_update_decision_stores_resolution():
    ctx, mgr = _patched_sm()
    with ctx:
        apply_resolution("t1", "sess-1", "halt", "blocked")
    mgr.update_decision.assert_called_once()
    call_args = mgr.update_decision.call_args[0]
    assert call_args[0] == "sess-1"
    assert call_args[1] == "resolution_t1"
    data = call_args[2]
    assert data["resolution"] == "halt"
    assert data["justification"] == "blocked"
    assert "timestamp" in data


def test_resolved_by_default_is_orchestrator():
    ctx, mgr = _patched_sm()
    with ctx:
        apply_resolution("t1", "sess-1", "proceed", "ok")
    data = mgr.update_decision.call_args[0][2]
    assert data["resolved_by"] == "orchestrator"


def test_resolved_by_custom():
    ctx, mgr = _patched_sm()
    with ctx:
        apply_resolution("t1", "sess-1", "proceed", "ok", resolved_by="cli")
    data = mgr.update_decision.call_args[0][2]
    assert data["resolved_by"] == "cli"


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

def test_audit_log_emitted_when_provided():
    mock_audit = MagicMock()
    ctx, mgr = _patched_sm()
    with ctx:
        apply_resolution("t1", "sess-1", "proceed", "ok", audit_log=mock_audit)
    mock_audit.append_event.assert_called_once()
    event_type, data = mock_audit.append_event.call_args[0]
    assert event_type == "disagreement_resolved"
    assert data["op"] == "disagreement_resolved"
    assert data["task_ref"] == "t1"
    assert data["resolution"] == "proceed"


def test_audit_log_none_no_call():
    """No audit_log → append_event never called, no AttributeError."""
    ctx, _ = _patched_sm()
    with ctx:
        result = apply_resolution("t1", "sess-1", "proceed", "ok", audit_log=None)
    assert result["status"] == "resolved"


# ---------------------------------------------------------------------------
# Error propagation
# ---------------------------------------------------------------------------

def test_session_not_found_propagates():
    ctx, mgr = _patched_sm()
    mgr.load_session.side_effect = FileNotFoundError("No session: sess-x")
    with ctx:
        with pytest.raises(FileNotFoundError):
            apply_resolution("t1", "sess-x", "proceed", "ok")

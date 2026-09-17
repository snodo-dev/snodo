"""Tests for surfacing coder reports in the halt payload and task show (ADR 048, #319).

FILE: tests/engine/test_coder_report_surfaced.py
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from snodo.compiler.models import Mode, Protocol, Validator
from snodo.core.interfaces import Task
from snodo.engine.loop import GraphBuilder
from snodo.engine.state import LoopState
from snodo.coders.report import CoderReport, CoderFileChange, STOP_REASONS, print_coder_report


def _make_builder():
    protocol = Protocol(
        protocol_id="test",
        name="Test",
        modes=[Mode(mode_id="producer", name="Producer", tools=[], validators=[])],
        validators=[
            Validator(
                validator_id="v1",
                validator_type="security",
                evaluation_phase="pre_execute",
            )
        ],
        initial_mode="producer",
    )
    builder = GraphBuilder(protocol)
    session = MagicMock()
    session.checkpoint.decisions = {}
    mgr = MagicMock()
    mgr.load_session.return_value = session
    builder._session_manager = mgr
    builder._session_id = "sess-1"
    return builder


def test_halt_payload_without_report_has_no_coder_report_key():
    """A run without a report produces a halt payload with no coder_report key at all."""
    builder = _make_builder()
    task = Task(id="t1", spec="do work")
    loop_state = LoopState(task=task, current_mode="producer")
    loop_state.is_blocked = True
    loop_state.halt_type = "no_file_operations"

    payload = builder._build_halt_payload(loop_state)
    assert "coder_report" not in payload


def test_halt_payload_with_malformed_report_is_discarded():
    """A malformed report is discarded and omitted from the halt payload without raising."""
    builder = _make_builder()
    task = Task(id="t1", spec="do work")
    loop_state = LoopState(task=task, current_mode="producer")
    loop_state.is_blocked = True
    loop_state.halt_type = "no_file_operations"
    loop_state.metadata["coder_report"] = {"stop_reason": "not_a_real_stop_reason"}

    payload = builder._build_halt_payload(loop_state)
    assert "coder_report" not in payload


def test_halt_payload_surfaces_coder_report_and_disagreement():
    """Halt payload contains coder_report, evidence labels, and disagreement naming."""
    builder = _make_builder()
    task = Task(id="t1", spec="do work")
    loop_state = LoopState(task=task, current_mode="producer")
    loop_state.is_blocked = True
    loop_state.halt_type = "verification_failed"
    loop_state.artifacts = ["written.py", "git_commit"]
    loop_state.metadata["coder_report"] = {
        "stop_reason": "completed",
        "turns_used": 3,
        "turns_available": 10,
        "tokens_used": 1500,
        "context_window": 8000,
        "wall_time_ms": 4200,
        "files": [
            {"path": "written.py", "kind": "modified"},
            {"path": "phantom.py", "kind": "created"},
        ],
    }
    loop_state.metadata["attempt_written_files"] = ["written.py", "unclaimed.py"]

    payload = builder._build_halt_payload(loop_state)
    assert "coder_report" in payload
    cr = payload["coder_report"]

    assert cr["source"] == "coder"
    assert cr["evidence_only"] is True
    assert cr["stop_reason"] == "completed"
    assert cr["turns_used"] == 3
    assert cr["turns_available"] == 10
    assert cr["tokens_used"] == 1500
    assert cr["context_window"] == 8000
    assert cr["wall_time_ms"] == 4200
    assert cr["claimed_but_missing"] == ["phantom.py"]
    assert cr["unclaimed_but_present"] == ["unclaimed.py"]
    assert cr["disagreement"] == {
        "claimed_but_missing": ["phantom.py"],
        "unclaimed_but_present": ["unclaimed.py"],
    }


def test_halt_payload_disagreement_with_workspace_disk_probe():
    """claimed_but_missing checks workspace.file_exists when available."""
    builder = _make_builder()
    ws = MagicMock()
    # "exists_on_disk.py" exists on disk but was not in loop_state.artifacts
    ws.file_exists.side_effect = lambda p: p == "exists_on_disk.py"
    builder.workspace_mcp = ws

    task = Task(id="t1", spec="do work")
    loop_state = LoopState(task=task, current_mode="producer")
    loop_state.is_blocked = True
    loop_state.halt_type = "no_file_operations"
    loop_state.artifacts = []
    loop_state.metadata["coder_report"] = {
        "stop_reason": "turn_budget",
        "turns_used": 5,
        "files": [
            {"path": "exists_on_disk.py", "kind": "created"},
            {"path": "truly_missing.py", "kind": "created"},
        ],
    }

    payload = builder._build_halt_payload(loop_state)
    cr = payload["coder_report"]
    # exists_on_disk.py is found via workspace_mcp, truly_missing.py is not
    assert "exists_on_disk.py" not in cr["claimed_but_missing"]
    assert cr["claimed_but_missing"] == ["truly_missing.py"]


def test_no_file_operations_path_preserves_coder_report():
    """On no_file_operations, the coder report is attached to metadata and surfaced."""
    builder = _make_builder()
    mock_coder = MagicMock()
    mock_coder.last_output_tail = "I could not find anything to change."
    mock_coder.last_report = CoderReport(
        stop_reason="completed",
        turns_used=2,
        files=[CoderFileChange(path="missed.py", kind="created")],
    )
    mock_coder.implement.return_value = MagicMock(files=[])

    ws = MagicMock()
    ws.file_exists.return_value = False
    builder.workspace_mcp = ws
    builder._existing_task_branch_work = MagicMock(return_value=None)
    task = Task(id="t1", spec="do work")

    from snodo.engine.nodes.executor import NoFileOperationsError

    with pytest.raises(NoFileOperationsError):
        builder._default_executor(
            task=task,
            token=MagicMock(),
            coder=mock_coder,
            workspace_mcp=ws,
            git_mcp=MagicMock(),
        )

    assert builder._last_coder_report is not None
    assert builder._last_coder_report.stop_reason == "completed"

    # Now simulate execute_step exception handler attaching it to loop_state
    loop_state = LoopState(task=task, current_mode="producer")
    loop_state.is_blocked = True
    loop_state.halt_type = "no_file_operations"
    loop_state.metadata["coder_report"] = builder._last_coder_report.model_dump()

    payload = builder._build_halt_payload(loop_state)
    assert "coder_report" in payload
    assert payload["coder_report"]["stop_reason"] == "completed"
    assert payload["coder_report"]["claimed_but_missing"] == ["missed.py"]


def test_print_coder_report_formatting(capsys):
    """print_coder_report formats evidence and disagreements clearly."""
    report = {
        "stop_reason": "context_budget",
        "turns_used": 4,
        "turns_available": 10,
        "tokens_used": 8192,
        "wall_time_ms": 5400,
        "files": [{"path": "app.py", "kind": "modified"}],
        "claimed_but_missing": ["missing.py"],
        "unclaimed_but_present": ["extra.py"],
    }
    print_coder_report(report)
    out = capsys.readouterr().out

    assert "coder_report (coder's account, evidence only):" in out
    assert "stop_reason:           context_budget" in out
    assert "turns:                 4 / 10" in out
    assert "tokens:                8192" in out
    assert "wall_time:             5400ms" in out
    assert "claimed_files:         app.py (modified)" in out
    assert "claimed-but-missing:   missing.py" in out
    assert "unclaimed-but-present: extra.py" in out


def test_print_coder_report_none_or_empty(capsys):
    """print_coder_report on None prints nothing."""
    print_coder_report(None)
    out = capsys.readouterr().out
    assert out == ""


def test_closed_vocabulary_invariants():
    """Ensure no verdict/status field can be added to CoderReport and STOP_REASONS is closed."""
    expected_reasons = {"completed", "turn_budget", "context_budget", "provider_fault", "abandoned"}
    assert STOP_REASONS == expected_reasons

    # Extra fields are forbidden by pydantic model_config
    with pytest.raises(Exception):
        CoderReport(status="passed")  # type: ignore

    with pytest.raises(Exception):
        CoderReport(passed=True)  # type: ignore

    with pytest.raises(Exception):
        CoderReport(severity="high")  # type: ignore

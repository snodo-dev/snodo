"""Tests for the pure task outcome classification projection."""

from snodo.jobs.outcome import TaskOutcome, classify_outcome


def test_success_and_clean_exit_fallback():
    assert classify_outcome({"status": "completed", "exit_code": 0}, []).situation == "success"
    assert classify_outcome({"exit_code": 0}, []).situation == "success"


def test_recovered_warning_returns_recovery_id_and_reason():
    result = classify_outcome(
        {"status": "finished", "halt": {"reason": "original reason"}},
        [{"event_type": "subtask_spawned", "task_ref": "task-2"}],
    )
    assert result == TaskOutcome("warn_recovered", "task-2", "original reason")


def test_adjudicable_escalation_waits_for_human():
    result = classify_outcome(
        {"halt": {"adjudicable": True}}, [{"event_type": "disagreement_escalated"}]
    )
    assert result.situation == "human_decision"


def test_unresolved_halt_is_blocked():
    assert classify_outcome({"halt": {"type": "other"}}, []).situation == "blocked"


def test_engine_fault_halt_types():
    for halt_type in ("validator_error", "internal_error", "environment_error"):
        assert classify_outcome({"halt": {"type": halt_type}}, []).situation == "engine_fault"


def test_recovery_exhausted_or_stalled_is_blocked():
    for event_type in ("recovery_exhausted", "recovery_stalled"):
        result = classify_outcome(
            {"status": "finished"},
            [{"event_type": "subtask_spawned", "task_ref": "task-2"},
             {"event_type": event_type}],
        )
        assert result.situation == "blocked"


def test_malformed_inputs_fail_closed():
    for state, events in ((None, None), ([], {}), ({"status": "failed"}, [None, 2])):
        result = classify_outcome(state, events)
        assert result == TaskOutcome("blocked", None, None)


def test_list_valued_fields_never_raise():
    result = classify_outcome(
        {"status": ["completed"], "exit_code": [0],
         "halt": {"type": ["internal_error"], "reason": ["bad"], "adjudicable": []}},
        [{"event_type": ["recovery_exhausted"], "task_ref": []}],
    )
    assert result == TaskOutcome("blocked", None, None)


def test_legacy_halt_type_and_event_op_are_supported():
    result = classify_outcome(
        {"halt": {"halt_type": "internal_error"}}, [{"op": "disagreement_escalated"}]
    )
    assert result.situation == "engine_fault"

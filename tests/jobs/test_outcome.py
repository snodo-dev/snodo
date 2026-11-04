"""Classification of persisted task outcomes."""

from snodo.jobs.outcome import classify_outcome


def test_classifies_success_and_fallback_clean_exit():
    assert classify_outcome({"status": "completed", "exit_code": 0}, []).situation == "success"
    assert classify_outcome({"exit_code": 0}, []).situation == "success"


def test_completed_recovery_is_warn_and_returns_recovery_id_and_reason():
    result = classify_outcome(
        {"status": "completed", "exit_code": 0, "halt": {"reason": "warning"}},
        [{"event_type": "subtask_spawned", "task_ref": "t_fix"}],
    )
    assert (result.situation, result.recovery_task_id, result.halt_reason) == (
        "warn_recovered", "t_fix", "warning",
    )


def test_adjudicable_escalation_needs_human_decision():
    result = classify_outcome(
        {"status": "halted", "halt": {"type": "escalated", "adjudicable": True}},
        [{"event_type": "disagreement_escalated"}],
    )
    assert result.situation == "human_decision"


def test_halt_without_recovery_or_decision_is_blocked():
    assert classify_outcome({"status": "halted", "halt": {"type": "blocked"}}, []).situation == "blocked"


def test_engine_faults_are_not_blocked():
    for halt_type in ("validator_error", "internal_error", "environment_error"):
        assert classify_outcome({"halt": {"type": halt_type}}, []).situation == "engine_fault"


def test_recovery_exhausted_or_stalled_is_blocked():
    for event_type in ("recovery_exhausted", "recovery_stalled"):
        assert classify_outcome(
            {"status": "halted"}, [{"event_type": "subtask_spawned", "task_ref": "t_fix"},
                                     {"event_type": event_type}],
        ).situation == "blocked"


def test_malformed_inputs_never_raise_and_fail_closed():
    for state, events in ((None, None), ([], {}), ({"status": "failed"}, [None, 2])):
        result = classify_outcome(state, events)
        assert result.situation == "blocked"
        assert result.recovery_task_id is None
        assert result.halt_reason is None


def test_unhashable_malformed_fields_never_raise():
    result = classify_outcome(
        {"status": ["completed"], "exit_code": 1, "halt": {"type": ["validator_error"]}},
        [{"event_type": ["recovery_exhausted"]}],
    )
    assert result.situation == "blocked"

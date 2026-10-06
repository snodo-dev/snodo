from snodo.infrastructure.next_actions import HALT_OUTCOMES, next_actions_for_halt


def halt(outcome):
    return {"final_decision": outcome, "task_id": "task-123"}


def test_escalate_requires_human_authorization():
    actions = next_actions_for_halt(halt("escalate"))
    assert actions[0].command == "snodo authorize task-123"


def test_blocker_non_plan_includes_append_spec_retry():
    actions = next_actions_for_halt(halt("blocker"))
    assert "specification or code" in actions[0].instruction
    assert actions[0].command == "snodo run --retry task-123 --append-spec '<guidance>'"


def test_blocker_plan_fixes_forward_without_one_off_command():
    actions = next_actions_for_halt(halt("blocker"), in_plan=True)
    assert "same plan" in actions[0].instruction
    assert actions[0].command is None


def test_validator_error_repairs_then_retries():
    actions = next_actions_for_halt(halt("validator_error"))
    assert "Repair the validator" in actions[0].instruction
    assert actions[0].command == "snodo run --retry task-123"


def test_environment_error_repairs_then_retries():
    actions = next_actions_for_halt(halt("environment_error"))
    assert "Repair the execution environment" in actions[0].instruction
    assert actions[0].command == "snodo run --retry task-123"


def test_internal_error_repairs_then_retries():
    actions = next_actions_for_halt(halt("internal_error"))
    assert "engine fault" in actions[0].instruction
    assert actions[0].command == "snodo run --retry task-123"


def test_every_closed_halt_outcome_is_handled():
    assert {outcome for outcome in HALT_OUTCOMES if next_actions_for_halt(halt(outcome))} == HALT_OUTCOMES

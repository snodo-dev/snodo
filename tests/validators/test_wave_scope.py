from types import SimpleNamespace

from snodo.compiler.models import Mode, Protocol, Validator
from snodo.core.interfaces import Task, ValidatorResult
from snodo.validators.runner import run_validators


def _protocol():
    return Protocol(
        protocol_id="wave-scope",
        name="Wave scope",
        version="1.0.0",
        modes=[
            Mode(
                mode_id="producer",
                name="Producer",
                tools=["dispatch"],
                validators=["wave", "task"],
            )
        ],
        validators=[
            Validator(
                validator_id="wave",
                validator_type="security",
                criteria=["The wave is safe"],
                scope="wave",
            ),
            Validator(
                validator_id="task",
                validator_type="security",
                criteria=["The task is safe"],
            ),
        ],
        initial_mode="producer",
    )


def _config():
    return SimpleNamespace(max_tokens=100, max_tool_turns=1)


def test_wave_judge_is_evaluated_once_and_carried_to_three_tasks():
    protocol = _protocol()
    calls = []

    def dispatch(validator, context, _registry):
        calls.append((validator.validator_id, context.task.id))
        return ValidatorResult(
            validator_id=validator.validator_id,
            severity="pass",
            justification="pass",
        )

    wave_result, _ = run_validators(
        protocol,
        [protocol.validators[0]],
        Task(id="wave:1", spec="task one\ntask two\ntask three"),
        current_mode="producer",
        validator_config=_config(),
        dispatch_fn=dispatch,
    )
    wave_results = {result.validator_id: result.model_dump() for result in wave_result}

    for task_id in ("one", "two", "three"):
        run_validators(
            protocol,
            protocol.validators,
            Task(id=task_id, spec=task_id),
            current_mode="producer",
            validator_config=_config(),
            dispatch_fn=dispatch,
            wave_results=wave_results,
        )

    assert [task_id for validator_id, task_id in calls if validator_id == "wave"] == ["wave:1"]


def test_task_judge_still_runs_once_per_task_in_same_wave():
    protocol = _protocol()
    calls = []

    def dispatch(validator, context, _registry):
        calls.append((validator.validator_id, context.task.id))
        return ValidatorResult(
            validator_id=validator.validator_id,
            severity="pass",
            justification="pass",
        )

    wave_result, _ = run_validators(
        protocol,
        [protocol.validators[0]],
        Task(id="wave:1", spec="wave"),
        current_mode="producer",
        validator_config=_config(),
        dispatch_fn=dispatch,
    )
    wave_results = {result.validator_id: result.model_dump() for result in wave_result}
    for task_id in ("one", "two", "three"):
        run_validators(
            protocol,
            protocol.validators,
            Task(id=task_id, spec=task_id),
            current_mode="producer",
            validator_config=_config(),
            dispatch_fn=dispatch,
            wave_results=wave_results,
        )

    assert [task_id for validator_id, task_id in calls if validator_id == "task"] == [
        "one", "two", "three"
    ]

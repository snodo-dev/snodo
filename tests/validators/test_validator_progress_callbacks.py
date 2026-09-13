"""Progress and verdict callbacks are separate, always delivered, never fatal.

A validator used to narrate its tool turns into ``context.progress_callback``,
which the runner had overloaded to also carry verdicts (a two-argument
callback).  The narration therefore raised ``TypeError`` on every turn, was
swallowed into a debug log, and an operator watching a run never saw that the
validators were doing anything.  These tests pin the split:

- a tool-using validator's turn narration reaches the progress sink;
- a validator that declares no tools still reports starting and finishing;
- a sink that raises is reported once and never stops validation.
"""

from unittest.mock import MagicMock

from snodo.compiler.models import Validator
from snodo.core.interfaces import Task, ValidatorResult
from snodo.validators.runner import run_validators


def _protocol(validator: Validator) -> MagicMock:
    protocol = MagicMock()
    protocol.get_mode.return_value = MagicMock(
        name="producer",
        tools=[],
        transitions={},
        validators=[validator.validator_id],
    )
    return protocol


def _tool_response(tool_calls):
    resp = MagicMock()
    msg = MagicMock()
    msg.content = ""
    msg.tool_calls = tool_calls
    resp.choices = [MagicMock()]
    resp.choices[0].message = msg
    resp.usage = MagicMock(prompt_tokens=10, completion_tokens=5)
    return resp


def _tool_call(call_id, name, arguments):
    tc = MagicMock()
    tc.id = call_id
    tc.function.name = name
    tc.function.arguments = arguments
    return tc


def test_tool_validator_turn_narration_reaches_progress_sink():
    """The turn line the validator writes is delivered — not lost to a TypeError."""
    validator = Validator(
        validator_id="security",
        validator_type="security",
        evaluation_phase="pre_execute",
        criteria=["Check the thing"],
        tools=["read_file"],
    )
    completion = MagicMock(side_effect=[
        _tool_response([_tool_call("c1", "read_file", '{"path": "src/app.py"}')]),
        _tool_response([
            _tool_call(
                "c2",
                "submit_verdict",
                '{"severity": "pass", "justification": "Looks good"}',
            )
        ]),
    ])

    workspace = MagicMock()
    workspace.project_root = "/tmp/project"
    workspace.read_file.return_value = "print(1)"

    narration: list[str] = []
    verdicts: list[tuple[str, str]] = []

    results, _ = run_validators(
        protocol=_protocol(validator),
        validators=[validator],
        task=Task(id="t1", spec="do the thing"),
        phase="pre_execute",
        completion_fn=completion,
        validator_config=MagicMock(max_tokens=1500, max_tool_turns=6),
        workspace_mcp=workspace,
        git_mcp=MagicMock(),
        current_mode="producer",
        progress_cb=narration.append,
        verdict_cb=lambda vid, result: verdicts.append((vid, result.severity)),
    )

    assert results[0].severity == "pass"
    assert any("Turn 1: read_file(src/app.py)" in line for line in narration), narration
    assert ("security", "pass") in verdicts


def test_no_tools_validator_reports_starting_and_finishing():
    """A validator with nothing to narrate is still visible as out and done."""
    validator = Validator(
        validator_id="quality",
        validator_type="security",
        evaluation_phase="pre_execute",
        criteria=["Check the thing"],
        tools=[],
    )
    narration: list[str] = []

    def dispatch(v_spec, ctx, reg):
        return ValidatorResult(
            validator_id=v_spec.validator_id, severity="pass", justification="ok"
        )

    results, _ = run_validators(
        protocol=_protocol(validator),
        validators=[validator],
        task=Task(id="t1", spec="do the thing"),
        phase="pre_execute",
        completion_fn=None,
        validator_config=MagicMock(max_tokens=1500, max_tool_turns=6),
        current_mode="producer",
        dispatch_fn=dispatch,
        progress_cb=narration.append,
    )

    assert results[0].severity == "pass"
    assert any("quality: started" in line for line in narration), narration
    assert any("quality: finished" in line for line in narration), narration


def test_raising_progress_sink_does_not_stop_validation(capsys):
    """A broken sink is reported once and the quorum runs to completion."""
    validator = Validator(
        validator_id="quality",
        validator_type="security",
        evaluation_phase="pre_execute",
        criteria=["Check the thing"],
        tools=[],
    )

    calls = {"n": 0}

    def broken(msg):
        calls["n"] += 1
        raise RuntimeError("sink is broken")

    def dispatch(v_spec, ctx, reg):
        return ValidatorResult(
            validator_id=v_spec.validator_id, severity="pass", justification="ok"
        )

    results, _ = run_validators(
        protocol=_protocol(validator),
        validators=[validator],
        task=Task(id="t1", spec="do the thing"),
        phase="pre_execute",
        completion_fn=None,
        validator_config=MagicMock(max_tokens=1500, max_tool_turns=6),
        current_mode="producer",
        dispatch_fn=dispatch,
        progress_cb=broken,
    )

    assert results[0].severity == "pass"
    err = capsys.readouterr().err
    assert err.count("sink failed") == 1, err

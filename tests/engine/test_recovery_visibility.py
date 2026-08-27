from unittest.mock import MagicMock
from snodo.compiler.models import Protocol, Mode, DisagreementPolicy, Validator
from snodo.core.interfaces import Task, ValidatorResult
from snodo.engine.loop import GraphBuilder, LoopState


def test_validator_verdict_cb_formatting():
    """_validator_verdict_cb prints warnings/blockers always and pass only when verbose."""
    messages = []
    def mock_progress(msg, verbose=False):
        messages.append((msg, verbose))

    loop = MagicMock(spec=GraphBuilder)
    loop._verbose = False
    loop._progress = mock_progress

    # Test pass verdict (should specify verbose=True)
    res_pass = ValidatorResult(validator_id="quality", severity="pass", justification="OK")
    GraphBuilder._validator_verdict_cb(loop, "quality", res_pass)
    assert len(messages) == 1
    assert messages[0] == ("    ✓ quality: pass", True)

    # Test warn verdict (should be un-gated verbose=False and carry icon + snippet)
    messages.clear()
    res_warn = ValidatorResult(validator_id="quality", severity="warn", justification="Tests failed (exit 2)\nStack trace detail")
    GraphBuilder._validator_verdict_cb(loop, "quality", res_warn)
    assert len(messages) == 1
    msg, verb = messages[0]
    assert "⚠️ quality: warn — Tests failed (exit 2)" in msg
    assert verb is False

    # Test blocker verdict
    messages.clear()
    res_blocker = ValidatorResult(validator_id="architecture", severity="blocker", justification="Forbidden import")
    GraphBuilder._validator_verdict_cb(loop, "architecture", res_blocker)
    assert len(messages) == 1
    msg, verb = messages[0]
    assert "❌ architecture: blocker — Forbidden import" in msg
    assert verb is False


def test_spawn_recovery_subtask_prints_progress():
    """_spawn_recovery_subtask surfaces recovery spawn, stall, and exhaustion transitions."""
    messages = []
    def mock_progress(msg, verbose=False):
        messages.append(msg)

    dummy_val = Validator(validator_id="quality", name="quality", validator_type="exec", command="echo ok")
    protocol = Protocol(
        protocol_id="test_proto",
        name="test_proto",
        initial_mode="build",
        disagreement_policy=DisagreementPolicy.UNANIMOUS,
        validators=[dummy_val],
        modes=[Mode(mode_id="build", name="build", validators=["quality"], max_recovery_depth=2)],
    )

    loop = GraphBuilder(protocol=protocol)
    loop._progress = mock_progress

    task = Task(id="t1", spec="test task", depth=0)
    loop_state = LoopState(task=task, current_mode="build")

    results = [ValidatorResult(validator_id="quality", severity="warn", justification="2 tests failed")]
    decision = MagicMock()

    # 1. First recovery attempt (depth 0 -> 1)
    loop._spawn_recovery_subtask(loop_state, results, decision)
    assert any("Recovery (attempt 1/2): spawned t1_fix_1 (quality (warn))" in m for m in messages)

    # 2. Repeated verdict -> Recovery stalled
    messages.clear()
    fix_task = loop_state.spawned_subtasks[0]
    loop_state_2 = LoopState(task=fix_task, current_mode="build")
    loop._spawn_recovery_subtask(loop_state_2, results, decision)
    assert any("Recovery stalled (attempt 2/2): identical validator verdict" in m for m in messages)

    # 3. Depth exhausted (depth 2 >= max 2)
    messages.clear()
    deep_task = Task(id="t1_fix_2", spec="test task", depth=2)
    loop_state_3 = LoopState(task=deep_task, current_mode="build")
    loop._spawn_recovery_subtask(loop_state_3, results, decision)
    assert any("Recovery depth exhausted (depth 2/2): limit reached; halting loop" in m for m in messages)

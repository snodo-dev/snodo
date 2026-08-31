"""Tests for hermetic execution under --mock / use_mock_coder (Fixes #12).

Verifies that under --mock / use_mock_coder=True:
- No provider calls escape to litellm.completion across coder, validators,
  classifier, and spec authoring rewriter.
- _build_completion_fn returns mock_completion_fn when mock mode is active,
  preventing future call sites from bypassing hermeticity.
- MockAdapter provides a hermetic _completion_fn.
"""

import subprocess
from unittest.mock import MagicMock

import litellm
import pytest
from snodo.coders.mock import (
    MockAdapter,
    is_mock_completion_fn,
    is_mock_mode_active,
    mock_completion_fn,
    set_mock_mode,
)
from snodo.compiler.models import Mode, Protocol, Validator
from snodo.core.interfaces import Task, ValidatorResult
from snodo.engine.loop import _build_completion_fn, build_protocol_graph
from snodo.validators.context import ValidatorContext
from snodo.validators.llm_validator import LLMValidator


@pytest.fixture(autouse=True)
def guard_litellm_network(monkeypatch):
    """Monkeypatch litellm.completion to fail loud if any network call escapes."""
    def _escaped_call(*args, **kwargs):
        raise RuntimeError("NETWORK CALL ESCAPED TO LITELLM UNDER MOCK MODE!")

    monkeypatch.setattr(litellm, "completion", _escaped_call)


def test_mock_adapter_provides_mock_completion_fn():
    """MockAdapter exposes _completion_fn which is marked as mock."""
    adapter = MockAdapter()
    assert hasattr(adapter, "_completion_fn")
    assert is_mock_completion_fn(adapter._completion_fn)
    assert is_mock_completion_fn(getattr(adapter, "completion_fn", None)) is False or is_mock_completion_fn(adapter._completion_fn)


def test_build_completion_fn_preserves_hermetic_mock():
    """_build_completion_fn returns mock_completion_fn without live binding when mock active."""
    set_mock_mode(True)
    try:
        fn = _build_completion_fn("gpt-4o", mock_completion_fn)
        assert is_mock_completion_fn(fn)

        # Even with a non-mock base_fn, when global mock mode is active, it returns a mock fn
        fn_any = _build_completion_fn("claude-3-5-sonnet", lambda *a, **k: None)
        assert is_mock_completion_fn(fn_any)
    finally:
        set_mock_mode(False)


def test_graph_execution_under_use_mock_coder_is_hermetic(monkeypatch, tmp_path):
    """Full protocol graph execution under use_mock_coder=True makes ZERO live LLM calls."""
    protocol = Protocol(
        protocol_id="test_mock_p",
        name="Test Mock Protocol",
        version="1.0.0",
        initial_mode="producer",
        modes=[Mode(mode_id="producer", name="Producer", tools=["edit"])],
        validators=[Validator(validator_id="quality", validator_type="quality")],
    )

    # Run against an isolated fixture repo, never the suite repo: the graph
    # classifies a wave and writes .snodo/wave.json under its project_root,
    # and a project_root defaulting to cwd would write into the repository
    # running the tests (Fixes #65).
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "README.md").write_text("init\n")
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=tmp_path, check=True)

    graph = build_protocol_graph(
        protocol=protocol,
        project_root=str(tmp_path),
        use_mock_coder=True,
    ).compile()
    task = Task(id="t_hermetic_1", spec="build feature")

    # Run graph execution — classifier, validators, and coder must all use mock completion
    state = graph.invoke({"task": task.model_dump(), "current_mode": "producer"})

    assert state is not None
    # Process global mock mode remains unmutated (False)
    assert is_mock_mode_active() is False


def test_mock_graph_followed_by_real_graph_in_same_process(tmp_path):
    """Building a mock graph followed by a real graph in the same process leaves the second graph non-mock."""
    from snodo.coders.litellm import LiteLLMAdapter
    from snodo.engine.loop import GraphBuilder

    protocol = Protocol(
        protocol_id="test_isolation",
        name="Test Isolation Protocol",
        version="1.0.0",
        initial_mode="producer",
        modes=[Mode(mode_id="producer", name="Producer", tools=["edit"])],
        validators=[Validator(validator_id="quality", validator_type="quality")],
    )

    # 1. Build first graph via build_protocol_graph with use_mock_coder=True
    build_protocol_graph(
        protocol=protocol,
        project_root=str(tmp_path),
        use_mock_coder=True,
    )
    assert is_mock_mode_active() is False

    # 2. Build second graph with real LiteLLMAdapter in same process
    coder_real = LiteLLMAdapter(model="gpt-4o")
    builder_real = GraphBuilder(protocol, coder=coder_real, project_root=str(tmp_path))
    assert not isinstance(builder_real.coder, MockAdapter)
    assert is_mock_completion_fn(builder_real._validator_runner._completion_fn) is False
    assert is_mock_mode_active() is False


def test_llm_validator_under_mock_mode_is_hermetic():
    """LLMValidator evaluation under mock mode uses mock_completion_fn."""
    set_mock_mode(True)
    try:
        val_spec = Validator(
            validator_id="security",
            validator_type="security",
            criteria=["No credentials in code"],
            tools=["read_file"],
        )
        validator = LLMValidator(validator_spec=val_spec)

        ctx = ValidatorContext(
            task=Task(id="task_val_1", spec="check code"),
            completion_fn=mock_completion_fn,
            workspace_mcp=MagicMock(),
            max_tool_turns=3,
        )

        res = validator.evaluate(ctx)
        assert isinstance(res, ValidatorResult)
        assert res.severity == "pass"
    finally:
        set_mock_mode(False)

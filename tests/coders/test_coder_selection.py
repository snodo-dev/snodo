"""Tests proving independent coder selection and resolution precedence (Fixes #144).

Covers:
1. Running the same protocol under two different coders (e.g. litellm vs opencode-cli) with only coder selection changed, while validator model is identical in both.
2. Precedence hierarchy: CLI choice > Mode.coder > Model prefix fallback > Default (litellm / mock).
3. Unknown coder name raises KeyError with message listing available registry keys.
4. Absence of deleted `create_coder` factory function across the codebase.
"""

from pathlib import Path
from unittest.mock import patch
import pytest

from snodo.coders import (
    CODER_REGISTRY,
    LiteLLMAdapter,
    OpenCodeCLIAdapter,
    get_coder,
    resolve_coder_name,
)
from snodo.compiler.models import Mode, Protocol, Validator
from snodo.engine.loop import GraphBuilder, build_protocol_graph


def _make_protocol(mode_coder: str = None) -> Protocol:
    v = Validator(
        validator_id="quality_llm",
        validator_type="quality",
        criteria=["Ensure code quality"],
    )
    m = Mode(
        mode_id="producer",
        name="Producer",
        tools=["edit"],
        validators=["quality_llm"],
        coder=mode_coder,
    )
    return Protocol(
        protocol_id="test_p",
        name="Test Protocol",
        version="1.0.0",
        initial_mode="producer",
        modes=[m],
        validators=[v],
        disagreement_policy="unanimous",
    )


def test_same_protocol_runs_under_two_coders_with_identical_validator_model():
    """The same protocol runs under two different coders with identical validator models."""
    protocol = _make_protocol()
    model = "gpt-4o"

    coder1 = get_coder(resolve_coder_name(model=model, cli_coder="litellm"), model=model)
    coder2 = get_coder(resolve_coder_name(model=model, cli_coder="opencode-cli"), model=model, workspace=Path("/tmp"))

    with patch("snodo.config.ConfigManager.load", return_value={"model": model}):
        builder1 = GraphBuilder(protocol=protocol, coder=coder1)
        builder2 = GraphBuilder(protocol=protocol, coder=coder2)

    # Coder classes differ according to selection
    assert isinstance(builder1.coder, LiteLLMAdapter)
    assert isinstance(builder2.coder, OpenCodeCLIAdapter)

    # Validator model is identical ("gpt-4o") in both runs
    assert builder1._validator_runner._default_model == "gpt-4o"
    assert builder2._validator_runner._default_model == "gpt-4o"

    # Also verify build_protocol_graph end-to-end resolves the selected coder
    with patch("snodo.engine.loop.GraphBuilder") as mock_gb:
        build_protocol_graph(protocol=protocol, model=model, coder_name="opencode-cli")
        _, kwargs = mock_gb.call_args
        assert isinstance(kwargs["coder"], OpenCodeCLIAdapter)


def test_coder_selection_precedence():
    """Assert precedence: Mock flag > CLI choice > Mode.coder > Model prefix fallback > Default."""
    # 1. Explicit --mock (use_mock=True) wins over explicit CLI choice (--coder agy), Mode.coder, and model prefix
    res0 = resolve_coder_name(
        model="gpt-4o",
        mode_coder="opencode",
        cli_coder="agy",
        use_mock=True,
    )
    assert res0 == "mock"

    # 2. Explicit CLI choice wins over Mode.coder and model prefix when use_mock is False
    res1 = resolve_coder_name(
        model="gpt-4o",
        mode_coder="opencode",
        cli_coder="opencode-cli",
    )
    assert res1 == "opencode-cli"

    # 3. Mode.coder wins over model prefix fallback when CLI choice is None
    res2 = resolve_coder_name(
        model="gpt-4o",
        mode_coder="opencode",
        cli_coder=None,
    )
    assert res2 == "opencode"

    # 4. Model prefix fallback wins when CLI choice and Mode.coder are None
    res3 = resolve_coder_name(
        model="opencode-cli/deepseek/deepseek-chat",
        mode_coder=None,
        cli_coder=None,
    )
    assert res3 == "opencode-cli"

    res3_openai = resolve_coder_name(model="gpt-4o")
    assert res3_openai == "openai"

    res3_anthropic = resolve_coder_name(model="claude-sonnet-4-20250514")
    assert res3_anthropic == "anthropic"

    res3_gemini = resolve_coder_name(model="gemini-2.5-pro")
    assert res3_gemini == "gemini"

    # 5. Default fallback when no prefix matches
    res4 = resolve_coder_name(model="unknown-provider/custom-model")
    assert res4 == "litellm"


def test_unknown_coder_name_fails_with_registry_keys_message():
    """An unknown coder name raises KeyError containing valid registry keys."""
    with pytest.raises(KeyError) as exc_info:
        get_coder("nonexistent_coder_backend")

    msg = str(exc_info.value)
    assert "Unknown coder 'nonexistent_coder_backend'" in msg
    assert "Available:" in msg
    for key in CODER_REGISTRY:
        assert key in msg


def test_create_coder_deleted_has_no_callers():
    """create_coder factory function is deleted and absent from snodo.coders and snodo.agents.adapter."""
    import snodo.coders
    import snodo.agents.adapter

    assert not hasattr(snodo.coders, "create_coder")
    assert not hasattr(snodo.agents.adapter, "create_coder")

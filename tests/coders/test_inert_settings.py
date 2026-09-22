"""An inert coder setting is named at selection, not after a failed run.

The ``llm.coder`` section presents one set of settings, but the coders are
not alike: ``max_tool_turns`` governs the in-process litellm family and
never reaches ``opencode-cli``, which spawns a program that runs its own
loop; ``timeout_seconds`` governs the subprocess coders and is dropped by
litellm; ``temperature`` reaches the subprocess and container adapters'
constructors, is stored, and is never read again. A setting the operator
wrote that the selected coder cannot honour must say so, by name and coder
name, where the coder is selected — while a setting left at its default
stays silent and a coder that honours the setting reports nothing
(Fixes #311).
"""

import logging
from unittest import mock

import pytest
from snodo.coders import CODER_REGISTRY, OpenCodeCLIAdapter
from snodo.coders.inert_settings import (
    explicit_coder_settings,
    inert_settings,
    report_inert_coder_settings,
)
from snodo.compiler.models import Mode, Protocol, Validator
from snodo.engine.loop import build_protocol_graph
from snodo.infrastructure.config import CoderConfig, LlmConfig

_INERT_LOGGER = "snodo.coders.inert_settings"


def _warning_text(caplog):
    return "\n".join(r.getMessage() for r in caplog.records)


def _make_signing_issuer():
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives.asymmetric import rsa
    from snodo.infrastructure.decisions import SigningDecisionRecordIssuer

    priv = rsa.generate_private_key(65537, 2048, backend=default_backend())
    return SigningDecisionRecordIssuer(priv), priv.public_key()


def _make_set_model_jwt(signing_issuer, proposed_model, scope="coder"):
    from datetime import datetime, timezone

    import jwt

    payload = {
        "iat": datetime.now(timezone.utc),
        "task_ref": "t1",
        "type": "set_model",
        "proposed_model": proposed_model,
        "scope": scope,
        "justification": "test",
        "resolved_by": "human",
    }
    return jwt.encode(payload, signing_issuer._private_key, algorithm="RS256")


# The map the ticket's substance rests on, established from the adapters:
# (coder, explicitly-set field) pairs that are inert.
INERT_PAIRS = [
    ("litellm", "sandboxed"),
    ("openai", "sandboxed"),
    ("anthropic", "sandboxed"),
    ("gemini", "sandboxed"),
    ("mock", "sandboxed"),
    ("opencode-cli", "sandboxed"),
    ("agy", "sandboxed"),
    ("litellm", "timeout_seconds"),
    ("openai", "timeout_seconds"),
    ("anthropic", "timeout_seconds"),
    ("gemini", "timeout_seconds"),
    ("opencode-cli", "max_tool_turns"),
    ("opencode-cli", "max_tokens"),
    ("agy", "max_tool_turns"),
    ("agy", "max_tokens"),
    ("opencode", "max_tool_turns"),
    ("opencode", "timeout_seconds"),
]

HONOURED_PAIRS = [
    ("litellm", "max_tool_turns"),
    ("litellm", "max_tokens"),
    ("opencode-cli", "timeout_seconds"),
    ("agy", "timeout_seconds"),
    ("opencode", "model"),
    ("opencode", "sandboxed"),
]


@pytest.mark.parametrize("coder, field", INERT_PAIRS)
def test_explicit_setting_a_coder_cannot_honour_is_named_with_the_coder(
    coder, field, caplog
):
    """A configured-but-inert pairing is reported, naming setting and coder."""
    value = (
        "ollama/qwen3:32b"
        if field == "model"
        else False
        if field == "sandboxed"
        else 42
    )
    cfg = CoderConfig(**{field: value})
    explicit = explicit_coder_settings(cfg)
    assert field in explicit, "a written field is explicit regardless of value"

    with caplog.at_level(logging.WARNING, logger=_INERT_LOGGER):
        reported = report_inert_coder_settings(coder, explicit)

    assert reported == [field]
    text = _warning_text(caplog)
    assert field in text
    assert coder in text
    assert f"llm.coder.{field}" in text


@pytest.mark.parametrize("coder, field", HONOURED_PAIRS)
def test_a_coder_that_honours_the_setting_reports_nothing(coder, field, caplog):
    value = (
        "ollama/qwen3:32b"
        if field == "model"
        else True
        if field == "sandboxed"
        else 42
    )
    cfg = CoderConfig(**{field: value})
    explicit = explicit_coder_settings(cfg)

    with caplog.at_level(logging.WARNING, logger=_INERT_LOGGER):
        reported = report_inert_coder_settings(coder, explicit)

    assert reported == []
    assert caplog.records == []


def test_a_default_setting_is_silent(caplog):
    """The same field, never written, is no one's expectation: silence."""
    explicit = explicit_coder_settings(CoderConfig())
    assert explicit == {}

    with caplog.at_level(logging.WARNING, logger=_INERT_LOGGER):
        reported = report_inert_coder_settings("opencode-cli", explicit)

    assert reported == []
    assert caplog.records == []


def test_writing_a_default_valued_setting_makes_it_explicit():
    """Explicitness is about who wrote it, not what was written: a field set
    to its own default value in config.yml is still an operator expectation
    and is weighed. ``model_fields_set`` is what distinguishes the two."""
    written = CoderConfig.model_validate({"max_tool_turns": 6})
    assert "max_tool_turns" in written.model_fields_set
    assert explicit_coder_settings(written) == {"max_tool_turns": 6}
    assert explicit_coder_settings(CoderConfig()) == {}


def test_stored_but_never_read_is_inert_as_surely_as_never_arrived():
    """``temperature`` reaches the subprocess adapter only through a mode's
    coder_config; the constructor stores it and nothing ever reads it, so it
    is not honoured — while litellm, which passes it on every call, is."""
    explicit = explicit_coder_settings(CoderConfig(), {"temperature": 0.2})
    assert explicit == {"temperature": 0.2}

    litellm_cls = CODER_REGISTRY["litellm"]
    subprocess_cls = CODER_REGISTRY["opencode-cli"]
    assert inert_settings(litellm_cls, explicit) == []
    assert inert_settings(subprocess_cls, explicit) == ["temperature"]


def test_concurrency_is_never_weighed_at_the_coder_surface():
    """``concurrency`` is the dispatcher's capacity ceiling (honoured by
    ``snodo plan run``), not a knob a coder reads; reporting it against a
    coder would accuse a setting that is real at its own level."""
    cfg = CoderConfig(concurrency=4)
    explicit = explicit_coder_settings(cfg)
    assert "concurrency" not in explicit
    for name in CODER_REGISTRY:
        assert inert_settings(CODER_REGISTRY[name], explicit) == []


def test_engine_injected_plumbing_is_not_a_setting_to_honour():
    explicit = explicit_coder_settings(
        CoderConfig(), {"workspace_mcp": object(), "progress_callback": None}
    )
    assert explicit == {}


def test_unknown_coder_name_stays_silent_for_the_report(caplog):
    """``get_coder`` rejects an unknown name loudly; the report is not that
    check and must not double-fire or crash."""
    with caplog.at_level(logging.WARNING, logger=_INERT_LOGGER):
        reported = report_inert_coder_settings(
            "no-such-coder", explicit_coder_settings(CoderConfig(max_tool_turns=9))
        )
    assert reported == []
    assert caplog.records == []


# ---------------------------------------------------------------------------
# The report fires at the points a coder is selected.
# ---------------------------------------------------------------------------


def _make_protocol(mode_coder=None, coder_config=None):
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
        coder_config=coder_config or {},
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


def test_graph_build_reports_the_inert_pairing_at_selection(monkeypatch, tmp_path, caplog):
    """The morning's fault, made visible at build time: config sets
    llm.coder.max_tool_turns, the selected coder is opencode-cli, which never
    receives the value."""
    monkeypatch.setenv("SNODO_HOME", str(tmp_path))
    (tmp_path / "config.yml").write_text(
        "llm:\n"
        "  coder:\n"
        "    max_tool_turns: 12\n"
    )
    with mock.patch("snodo.engine.loop.GraphBuilder"):
        with caplog.at_level(logging.WARNING, logger=_INERT_LOGGER):
            build_protocol_graph(protocol=_make_protocol(), coder_name="opencode-cli")

    text = _warning_text(caplog)
    assert "max_tool_turns" in text
    assert "opencode-cli" in text
    assert "llm.coder.max_tool_turns" in text


def test_graph_build_is_silent_when_the_setting_applies(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("SNODO_HOME", str(tmp_path))
    (tmp_path / "config.yml").write_text(
        "llm:\n"
        "  coder:\n"
        "    max_tool_turns: 12\n"
    )
    with mock.patch("snodo.engine.loop.GraphBuilder"):
        with caplog.at_level(logging.WARNING, logger=_INERT_LOGGER):
            build_protocol_graph(protocol=_make_protocol(), coder_name="litellm")

    assert caplog.records == []


def test_graph_build_is_silent_when_nothing_was_written(monkeypatch, tmp_path, caplog):
    """The same coder as the reported case, defaults untouched: silence."""
    monkeypatch.setenv("SNODO_HOME", str(tmp_path))
    (tmp_path / "config.yml").write_text("llm:\n  coder:\n    model: opencode-cli/x-model\n")
    with mock.patch("snodo.engine.loop.GraphBuilder"):
        with caplog.at_level(logging.WARNING, logger=_INERT_LOGGER):
            build_protocol_graph(protocol=_make_protocol(), coder_name="opencode-cli")

    assert caplog.records == []


def test_mode_coder_config_settings_are_reported_against_the_selected_coder(caplog):
    """The protocol's coder_config is an operator-written setting too: a
    temperature handed to opencode-cli is stored and never read."""
    explicit = explicit_coder_settings(CoderConfig(), {"temperature": 0.2})
    with caplog.at_level(logging.WARNING, logger=_INERT_LOGGER):
        reported = report_inert_coder_settings("opencode-cli", explicit)

    assert reported == ["temperature"]
    text = _warning_text(caplog)
    assert "temperature" in text
    assert "opencode-cli" in text


def test_respawn_reports_the_setting_against_the_new_coder(caplog):
    """A verified set_model override that selects opencode-cli mid-run is a
    coder selection: the config's max_tool_turns is inert for the new coder
    from that moment on, and the respawn says so."""
    from snodo.coders import LiteLLMAdapter
    from snodo.engine.loop import GraphBuilder
    from snodo.infrastructure.decisions import VerifyOnlyDecisionRecordIssuer
    from snodo.tools.workspace import WorkspaceMCP

    coder = LiteLLMAdapter(model="claude-sonnet-4-20250514")
    builder = GraphBuilder(_make_protocol(), coder=coder)
    signer, pub = _make_signing_issuer()
    builder._decision_issuer = VerifyOnlyDecisionRecordIssuer(pub)
    builder._authorized_decisions = [
        _make_set_model_jwt(signer, "opencode-cli/deepseek-v4-flash")
    ]
    builder.workspace_mcp = WorkspaceMCP(".")

    cfg = LlmConfig(coder=CoderConfig(max_tool_turns=40))
    with mock.patch("snodo.infrastructure.config.load_llm_config", return_value=cfg):
        with caplog.at_level(logging.WARNING, logger=_INERT_LOGGER):
            builder._maybe_respawn_coder()

    assert isinstance(builder.coder, OpenCodeCLIAdapter)
    text = _warning_text(caplog)
    assert "max_tool_turns" in text
    assert "opencode-cli" in text


def test_repeated_task_dispatches_under_unchanged_config_emit_notice_once(
    monkeypatch, tmp_path, caplog
):
    """Several tasks dispatch under one unchanged configuration; the inert-settings
    notice is emitted once across the dispatches, not per dispatch (Fixes #355)."""
    monkeypatch.setenv("SNODO_HOME", str(tmp_path))
    (tmp_path / "config.yml").write_text(
        "llm:\n"
        "  coder:\n"
        "    max_tool_turns: 100\n"
        "    max_tokens: 128000\n"
    )
    protocol = _make_protocol()
    with mock.patch("snodo.engine.loop.GraphBuilder"):
        with caplog.at_level(logging.WARNING, logger=_INERT_LOGGER):
            for _ in range(3):
                build_protocol_graph(protocol=protocol, coder_name="opencode-cli")

    records = [r for r in caplog.records if r.name == _INERT_LOGGER]
    assert len(records) == 2
    text = _warning_text(caplog)
    assert text.count("max_tool_turns") == 1
    assert text.count("max_tokens") == 1


def test_report_inert_coder_settings_emits_once_for_same_pairing(caplog):
    """Calling report_inert_coder_settings twice for the same coder and setting
    emits the warning on the first call and is silent on the second (Fixes #355)."""
    explicit = {"max_tool_turns": 42}
    with caplog.at_level(logging.WARNING, logger=_INERT_LOGGER):
        first = report_inert_coder_settings("opencode-cli", explicit)
        assert first == ["max_tool_turns"]
        assert len(caplog.records) == 1

        second = report_inert_coder_settings("opencode-cli", explicit)
        assert second == []
        assert len(caplog.records) == 1

        # A different coder with an inert setting is reported
        third = report_inert_coder_settings("agy", explicit)
        assert third == ["max_tool_turns"]
        assert len(caplog.records) == 2

        # A different setting for the first coder is reported
        fourth = report_inert_coder_settings("opencode-cli", {"max_tokens": 1000})
        assert fourth == ["max_tokens"]
        assert len(caplog.records) == 3

"""Failure-reason survival tests for the validator subsystem.

FILE: tests/validators/test_failure_reason_survival.py

For each handler that converts a failure into a safe value on a debugging
path, induce the failure and assert the reason (exception type and message)
reaches where a human looks: the log AND the surfaced result. A test that
only asserts the safe value was returned is the defect, not the check.
"""

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

from snodo.compiler.models import Validator
from snodo.core.interfaces import Task
from snodo.validators.context import ValidatorContext
from snodo.validators.llm_validator import LLMValidator
from snodo.validators.runner import dispatch_validator
from snodo.validators.protocol_adherence import ProtocolAdherenceValidator


def _recorded(caplog, needle: str) -> bool:
    return any(needle in r.getMessage() for r in caplog.records)


class _ProviderRejection(Exception):
    """Stands in for e.g. litellm refusing a request for a missing header."""


class _BrokenClass:
    def __init__(self, validator_spec=None):
        raise _ProviderRejection("Request is missing x-opencode-session")


class _Registry:
    def __init__(self, cls):
        self._cls = cls

    def lookup(self, validator_type):
        return self._cls


def _validator_spec(validator_type="security", criteria=("criterion one",)):
    return Validator(
        validator_id=f"val_{validator_type}",
        validator_type=validator_type,
        criteria=list(criteria),
    )


def _context(completion_fn):
    return ValidatorContext(
        task=Task(id="t1", spec="do the thing"),
        completion_fn=completion_fn,
        model="mock/test",
    )


class TestDispatchValidator:
    """The isolation boundary must convert, not consume, the failure."""

    def test_registry_class_failure_reason_reaches_log_and_result(self, monkeypatch, caplog):
        monkeypatch.setattr(
            "snodo.validators.llm_validator.LLMValidator", _BrokenClass
        )
        v = _validator_spec()
        with caplog.at_level(logging.WARNING, logger="snodo.validators.runner"):
            result = dispatch_validator(v, _context(MagicMock()), _Registry(_BrokenClass))

        assert result.error is True  # safe value still returned
        assert result.severity == "blocker"
        # The reason travels INTO the surfaced message, not only a log file.
        assert "_ProviderRejection" in result.justification
        assert "Request is missing x-opencode-session" in result.justification
        # ... and the caught exception's type and message reached the log.
        assert _recorded(caplog, "_ProviderRejection")
        assert _recorded(caplog, "Request is missing x-opencode-session")

    def test_llm_path_failure_reason_reaches_log_and_result(self, monkeypatch, caplog):
        monkeypatch.setattr(
            "snodo.validators.llm_validator.LLMValidator", _BrokenClass
        )
        v = _validator_spec()
        with caplog.at_level(logging.WARNING, logger="snodo.validators.runner"):
            result = dispatch_validator(v, _context(MagicMock()), _Registry(None))

        assert result.error is True
        assert "_ProviderRejection" in result.justification
        assert "Request is missing x-opencode-session" in result.justification
        assert _recorded(caplog, "LLM validation for")
        assert _recorded(caplog, "_ProviderRejection")


class TestLLMValidatorOperationalFault:
    """A provider error flattened into an error=True result must keep its cause."""

    def test_provider_error_type_and_message_survive_into_log_and_justification(self, caplog):
        completion = MagicMock(
            side_effect=_ProviderRejection("Request is missing x-opencode-session")
        )
        validator = LLMValidator(validator_spec=_validator_spec(), completion_fn=completion)
        with caplog.at_level(logging.DEBUG, logger="snodo.validators.llm_validator"):
            result = validator.evaluate(_context(completion))

        assert result.error is True
        assert result.severity == "blocker"
        assert "_ProviderRejection" in result.justification
        assert "Request is missing x-opencode-session" in result.justification
        assert _recorded(caplog, "operational fault")
        assert _recorded(caplog, "_ProviderRejection")

    def test_submit_verdict_unparseable_args_logs_reason(self, caplog):
        validator = LLMValidator(validator_spec=_validator_spec())
        tc = SimpleNamespace(
            function=SimpleNamespace(
                name="submit_verdict",
                arguments='{"severity": "pass", ',  # truncated tool-call JSON
            )
        )
        with caplog.at_level(logging.DEBUG, logger="snodo.validators.llm_validator"):
            result = validator._extract_submit_verdict([tc])

        assert result is None  # safe value keeps being returned
        assert _recorded(caplog, "submit_verdict arguments unparseable")
        assert _recorded(caplog, "JSONDecodeError")

    def test_json_fallback_chain_logs_parse_reasons(self, caplog):
        validator = LLMValidator(validator_spec=_validator_spec())
        with caplog.at_level(logging.DEBUG, logger="snodo.validators.llm_validator"):
            assert validator._try_json_parse("severity: pass, probably") is None

        assert _recorded(caplog, "direct JSON parse failed")
        assert _recorded(caplog, "JSONDecodeError")


class TestProtocolAdherenceFault:
    def test_operational_fault_reason_survives_into_log_and_result(self):
        from snodo.compiler.models import DisagreementPolicy, Mode, Protocol

        completion = MagicMock(
            side_effect=_ProviderRejection("upstream auth rejected the token shape")
        )
        validator_spec = _validator_spec(validator_type="protocol")
        mode = Mode(mode_id="build", name="Build", validators=[validator_spec.validator_id])
        protocol = Protocol(
            protocol_id="pa-test", name="pa-test", version="1.0.0",
            modes=[mode], validators=[validator_spec],
            disagreement_policy=DisagreementPolicy.UNANIMOUS,
            initial_mode="build", roles=[],
        )
        validator = ProtocolAdherenceValidator(
            validator_spec=validator_spec, completion_fn=completion,
        )
        ctx = _context(completion)
        ctx.current_mode = mode
        ctx.protocol = protocol
        records = []

        class _Capture(logging.Handler):
            def emit(self, record):
                records.append(record.getMessage())

        handler = _Capture()
        logger = logging.getLogger("snodo.validators.protocol_adherence")
        logger.addHandler(handler)
        previous = logger.level
        logger.setLevel(logging.DEBUG)
        try:
            result = validator.evaluate(ctx)
        finally:
            logger.removeHandler(handler)
            logger.setLevel(previous)

        assert result.error is True
        assert "_ProviderRejection" in result.justification
        assert "upstream auth rejected the token shape" in result.justification
        assert any("_ProviderRejection" in m for m in records)
        assert any("operational fault" in m for m in records)

"""Shared helpers for exercising the four-outcome validate_task contract.

FILE: tests/mcp/_validate_helpers.py

Provides a deterministic "passing validation" context so tests can exercise
the token-gated dispatch/commit flow without a real LLM or test suite.
"""

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from snodo.core.interfaces import ValidatorResult


def pass_completion_fn():
    """Return a completion function that always yields a 'pass' verdict."""
    msg = MagicMock()
    msg.content = '{"severity": "pass", "justification": "ok"}'
    response = MagicMock()
    response.choices = [MagicMock(message=msg)]
    return MagicMock(return_value=response)


def warn_completion_fn():
    """Return a completion function that always yields a 'warn' verdict."""
    msg = MagicMock()
    msg.content = '{"severity": "warn", "justification": "concern"}'
    response = MagicMock()
    response.choices = [MagicMock(message=msg)]
    return MagicMock(return_value=response)


def mock_validator_config():
    return MagicMock(max_tokens=1500, max_tool_turns=6)


@contextmanager
def validation_passing(server):
    """Patch the validator LLM + test runner so validate_task returns 'pass'."""
    with patch(
        "snodo.validators.runner.resolve_validator_completion",
        return_value=(pass_completion_fn(), "mock-model", mock_validator_config()),
    ), patch(
        "snodo.validators.llm_validator.supports_response_schema", return_value=False
    ), patch.object(
        server.shell, "run_tests",
        return_value=ValidatorResult(
            validator_id="test_runner", severity="pass", justification="ok",
        ),
    ):
        yield

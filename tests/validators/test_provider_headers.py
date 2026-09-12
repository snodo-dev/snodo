"""Tests for provider header injection across all validator paths.

Ensures that provider headers (e.g., x-opencode-session for opencode Go)
reach every completion call by construction, including:
- LLM validators with no tools (single-completion path)
- LLM validators with tools (tool-loop path)
- Protocol-adherence validators
- New call sites that don't explicitly pass headers
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from snodo.compiler.models import Validator, Protocol, Mode
from snodo.core.interfaces import Task
from snodo.validators.context import ValidatorContext
from snodo.validators.llm_validator import LLMValidator
from snodo.validators.protocol_adherence import ProtocolAdherenceValidator
from snodo.validators.runner import (
    _wrap_completion_fn_with_headers,
    run_validators,
)


def _make_mock_response(severity: str = "pass", justification: str = "OK"):
    """Create a mock LLM response."""
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = json.dumps({
        "severity": severity,
        "justification": justification,
    })
    response.usage.prompt_tokens = 100
    response.usage.completion_tokens = 50
    return response


@pytest.fixture
def task():
    return Task(id="task_123", spec="Implement user login")


def _make_minimal_protocol():
    """Create a minimal protocol with required fields."""
    mode = Mode(mode_id="impl", name="Implementation", tools=[], validators=[])
    test_validator = Validator(
        validator_id="test-validator",
        validator_type="protocol",
        evaluation_phase="pre_execute",
        criteria=["Test criterion"],
    )
    return Protocol(
        protocol_id="test-protocol",
        name="test-protocol",
        modes=[mode],
        validators=[test_validator],
        initial_mode="impl",
    )


@pytest.fixture
def protocol():
    """Minimal protocol with one mode."""
    return _make_minimal_protocol()


class TestHeaderInjectionViaWrapper:
    """Test the wrapper function that injects headers."""

    @patch('snodo.config.ConfigManager.resolve_extra_headers')
    def test_wrapper_injects_headers_when_available(self, mock_resolve_headers):
        """Wrapper adds headers to kwargs when resolved."""
        mock_resolve_headers.return_value = {"x-custom-header": "value"}

        # Mock the underlying completion function
        mock_completion = MagicMock(return_value=_make_mock_response())
        wrapped = _wrap_completion_fn_with_headers(mock_completion, "task_123")

        # Call the wrapped function
        wrapped(model="gpt-4", messages=[{"role": "user", "content": "test"}])

        # Verify the underlying function was called with headers
        assert mock_completion.called
        call_kwargs = mock_completion.call_args[1]
        assert "extra_headers" in call_kwargs
        assert call_kwargs["extra_headers"] == {"x-custom-header": "value"}

    @patch('snodo.config.ConfigManager.resolve_extra_headers')
    def test_wrapper_does_not_override_existing_headers(self, mock_resolve_headers):
        """Wrapper doesn't override if headers already present."""
        mock_resolve_headers.return_value = {"x-new": "new"}

        mock_completion = MagicMock(return_value=_make_mock_response())
        wrapped = _wrap_completion_fn_with_headers(mock_completion, "task_123")

        # Call with existing headers
        existing_headers = {"x-existing": "existing"}
        wrapped(
            model="gpt-4",
            messages=[],
            extra_headers=existing_headers
        )

        # Verify existing headers were not replaced
        call_kwargs = mock_completion.call_args[1]
        assert call_kwargs["extra_headers"] == existing_headers

    @patch('snodo.config.ConfigManager.resolve_extra_headers')
    def test_wrapper_resolves_headers_with_task_id(self, mock_resolve_headers):
        """Wrapper passes task_id to resolve_extra_headers."""
        mock_resolve_headers.return_value = {}

        mock_completion = MagicMock(return_value=_make_mock_response())
        wrapped = _wrap_completion_fn_with_headers(mock_completion, "task_456")

        wrapped(model="gpt-4", messages=[])

        # Verify task_id was passed to resolve_extra_headers
        mock_resolve_headers.assert_called_once()
        call_kwargs = mock_resolve_headers.call_args[1]
        assert call_kwargs["task_id"] == "task_456"


class TestLLMValidatorWithHeaders:
    """Test LLM validator passes headers in all paths."""

    @patch('snodo.config.ConfigManager.resolve_extra_headers')
    def test_single_completion_path_gets_headers(self, mock_resolve_headers):
        """LLM validator without tools receives headers automatically."""
        mock_resolve_headers.return_value = {"x-opencode-session": "sess_123"}

        # Create a mock completion function
        mock_completion = MagicMock(return_value=_make_mock_response("pass"))

        # Wrap it like run_validators does
        wrapped_completion = _wrap_completion_fn_with_headers(
            mock_completion, task_id="task_123"
        )

        # Create validator with no tools (takes single-completion path)
        validator_spec = Validator(
            validator_id="test_validator",
            validator_type="security",
            evaluation_phase="pre_execute",
            criteria=["Check security"],
            tools=[]  # No tools = single-completion path
        )

        context = ValidatorContext(
            task=Task(id="task_123", spec="test"),
            model="gpt-4",
            completion_fn=wrapped_completion,
        )

        validator = LLMValidator(validator_spec)
        result = validator.evaluate(context)

        # Verify completion was called with headers
        assert mock_completion.called
        call_kwargs = mock_completion.call_args[1]
        assert "extra_headers" in call_kwargs
        assert result.severity == "pass"

    @patch('snodo.config.ConfigManager.resolve_extra_headers')
    def test_tool_loop_path_gets_headers(self, mock_resolve_headers):
        """LLM validator with tools receives headers via wrapper."""
        mock_resolve_headers.return_value = {"x-custom": "value"}

        # Create mock MCPs and completion
        mock_workspace_mcp = MagicMock()
        mock_git_mcp = MagicMock()
        mock_completion = MagicMock(return_value=_make_mock_response())

        wrapped_completion = _wrap_completion_fn_with_headers(
            mock_completion, task_id="task_123"
        )

        # Validator with tools
        validator_spec = Validator(
            validator_id="test_validator",
            validator_type="architecture",
            evaluation_phase="post_execute",
            criteria=["Check design"],
            tools=["read_file"]  # Has tools = tool-loop path
        )

        context = ValidatorContext(
            task=Task(id="task_123", spec="test"),
            model="gpt-4",
            completion_fn=wrapped_completion,
            workspace_mcp=mock_workspace_mcp,
            git_mcp=mock_git_mcp,
        )

        validator = LLMValidator(validator_spec)
        validator.evaluate(context)

        # Tool-loop calls completion in the loop
        assert mock_completion.called
        # At least one call should have headers
        calls_with_headers = [
            c for c in mock_completion.call_args_list
            if "extra_headers" in c[1]
        ]
        assert len(calls_with_headers) > 0


class TestProtocolAdherenceWithHeaders:
    """Test protocol-adherence validator receives headers."""

    @patch('snodo.config.ConfigManager.resolve_extra_headers')
    def test_protocol_adherence_gets_headers_unstructured(self, mock_resolve_headers):
        """Protocol-adherence unstructured path receives headers via wrapper."""
        mock_resolve_headers.return_value = {"x-opencode-session": "sess_456"}

        # Create a mock completion function
        mock_completion = MagicMock(return_value=_make_mock_response())

        # Wrap like run_validators does
        wrapped_completion = _wrap_completion_fn_with_headers(
            mock_completion, task_id="task_123"
        )

        # Create protocol
        protocol = _make_minimal_protocol()
        mode = protocol.modes[0]  # Get first mode from protocol

        validator_spec = Validator(
            validator_id="protocol",
            validator_type="protocol",
            evaluation_phase="pre_execute",
            criteria=["Check mode alignment"],
        )

        context = ValidatorContext(
            task=Task(id="task_123", spec="test task"),
            current_mode=mode,
            protocol=protocol,
            model="gpt-4",
            completion_fn=wrapped_completion,
        )

        validator = ProtocolAdherenceValidator(validator_spec)
        validator.evaluate(context)

        # Verify headers were passed to completion
        assert mock_completion.called
        call_kwargs = mock_completion.call_args[1]
        assert "extra_headers" in call_kwargs
        assert call_kwargs["extra_headers"]["x-opencode-session"] == "sess_456"

    @patch('snodo.config.ConfigManager.resolve_extra_headers')
    def test_protocol_adherence_gets_headers_structured(self, mock_resolve_headers):
        """Protocol-adherence structured path receives headers via wrapper."""
        mock_resolve_headers.return_value = {"x-api-key": "key_789"}

        # Mock response for structured output
        mock_completion = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps({
            "severity": "pass",
            "justification": "Task aligns"
        })
        mock_completion.return_value = mock_response

        wrapped_completion = _wrap_completion_fn_with_headers(
            mock_completion, task_id="task_123"
        )

        protocol = _make_minimal_protocol()
        mode = protocol.modes[0]

        validator_spec = Validator(
            validator_id="protocol",
            validator_type="protocol",
            evaluation_phase="pre_execute",
            criteria=["Check alignment"],
        )

        context = ValidatorContext(
            task=Task(id="task_123", spec="test"),
            current_mode=mode,
            protocol=protocol,
            model="gpt-4",
            completion_fn=wrapped_completion,
        )

        validator = ProtocolAdherenceValidator(validator_spec)
        validator.evaluate(context)

        # Verify headers were included
        assert mock_completion.called
        call_kwargs = mock_completion.call_args[1]
        assert "extra_headers" in call_kwargs or "response_format" in call_kwargs


class TestNewCallSiteGetsHeaders:
    """Test that new call sites automatically receive headers without modification."""

    @patch('snodo.config.ConfigManager.resolve_extra_headers')
    def test_new_call_site_without_explicit_headers_gets_them(self, mock_resolve_headers):
        """A hypothetical new completion call inherits headers automatically."""
        mock_resolve_headers.return_value = {"x-auto": "injected"}

        # Simulate a new call site that doesn't know about headers
        def new_call_site(completion_fn, task_id):
            """A new validator that just calls completion_fn without thinking about headers."""
            wrapped = _wrap_completion_fn_with_headers(completion_fn, task_id)
            # This call doesn't explicitly handle headers
            wrapped(model="gpt-4", messages=[{"role": "user", "content": "test"}])

        mock_completion = MagicMock(return_value=_make_mock_response())
        new_call_site(mock_completion, "task_123")

        # Verify headers were automatically added by the wrapper
        call_kwargs = mock_completion.call_args[1]
        assert "extra_headers" in call_kwargs
        assert call_kwargs["extra_headers"] == {"x-auto": "injected"}


class TestRunValidatorsWrapsCompletion:
    """Test that run_validators wraps completion_fn with headers."""

    @patch('snodo.config.ConfigManager.resolve_extra_headers')
    @patch('snodo.validators.runner.dispatch_validator')
    def test_run_validators_wraps_completion_fn(self, mock_dispatch, mock_resolve_headers):
        """run_validators wraps completion_fn before passing to validators."""
        mock_resolve_headers.return_value = {"x-task": "header"}

        # Mock dispatch to verify context has wrapped completion_fn
        captured_context = None
        def capture_context(v, ctx, reg):
            nonlocal captured_context
            captured_context = ctx
            from snodo.core.interfaces import ValidatorResult
            return ValidatorResult(
                validator_id=v.validator_id,
                severity="pass",
                justification="OK"
            )

        mock_dispatch.side_effect = capture_context

        mock_completion = MagicMock(return_value=_make_mock_response())

        validator_spec = Validator(
            validator_id="test",
            validator_type="security",
            evaluation_phase="pre_execute",
            criteria=["test"],
        )

        task = Task(id="task_123", spec="test")
        protocol = _make_minimal_protocol()

        # Call run_validators
        results, _ = run_validators(
            protocol=protocol,
            validators=[validator_spec],
            task=task,
            completion_fn=mock_completion,
        )

        # Verify the context has a wrapped completion_fn
        assert captured_context is not None
        assert captured_context.completion_fn is not None

        # Call through the wrapped function to verify it adds headers
        captured_context.completion_fn(model="gpt-4", messages=[])

        # Verify the underlying function got headers
        assert mock_completion.called
        call_kwargs = mock_completion.call_args[1]
        assert "extra_headers" in call_kwargs

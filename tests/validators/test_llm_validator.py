"""Tests for the LLM Validator.

FILE: tests/validators/test_llm_validator.py (Task 6.1)

Tests cover: prompt construction, JSON parsing, fallback behavior,
severity validation, and integration with the engine loop.
"""

import json
from unittest.mock import MagicMock

import pytest

from snodo.compiler.models import Validator
from snodo.core.interfaces import Task
from snodo.validators.llm_validator import LLMValidator


# === Fixtures ===

@pytest.fixture
def security_validator():
    """A security validator spec from protocol YAML."""
    return Validator(
        validator_id="security",
        validator_type="security",
        evaluation_phase="pre_execute",
        criteria=[
            "Check for security vulnerabilities",
            "Validate input sanitization",
            "Check authentication/authorization",
        ],
    )


@pytest.fixture
def architecture_validator():
    """An architecture validator spec from protocol YAML."""
    return Validator(
        validator_id="architecture",
        validator_type="architecture",
        evaluation_phase="pre_execute",
        criteria=[
            "Check design patterns",
            "Validate separation of concerns",
        ],
    )


@pytest.fixture
def task():
    """A sample task."""
    return Task(id="task_abc123", spec="Implement user login with OAuth2")


def _make_llm_response(severity: str, justification: str) -> MagicMock:
    """Create a mock LLM response object."""
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = json.dumps({
        "severity": severity,
        "justification": justification,
    })
    return response


def _make_completion_fn(severity: str = "pass", justification: str = "All good"):
    """Create a mock completion function returning a fixed response."""
    mock_fn = MagicMock()
    mock_fn.return_value = _make_llm_response(severity, justification)
    return mock_fn


# === Prompt Construction Tests ===

class TestPromptConstruction:
    """Tests for _build_prompt()."""

    def test_includes_task_spec(self, security_validator, task):
        validator = LLMValidator(security_validator, _make_completion_fn())
        prompt = validator._build_prompt(task)
        assert task.spec in prompt

    def test_includes_all_criteria(self, security_validator, task):
        validator = LLMValidator(security_validator, _make_completion_fn())
        prompt = validator._build_prompt(task)
        for criterion in security_validator.criteria:
            assert criterion in prompt

    def test_includes_validator_type(self, security_validator, task):
        validator = LLMValidator(security_validator, _make_completion_fn())
        prompt = validator._build_prompt(task)
        assert "security" in prompt

    def test_includes_severity_options(self, security_validator, task):
        validator = LLMValidator(security_validator, _make_completion_fn())
        prompt = validator._build_prompt(task)
        assert '"pass"' in prompt
        assert '"warn"' in prompt
        assert '"blocker"' in prompt

    def test_includes_json_instruction(self, security_validator, task):
        validator = LLMValidator(security_validator, _make_completion_fn())
        prompt = validator._build_prompt(task)
        assert "JSON" in prompt

    def test_criteria_numbered(self, security_validator, task):
        validator = LLMValidator(security_validator, _make_completion_fn())
        prompt = validator._build_prompt(task)
        assert "1." in prompt
        assert "2." in prompt
        assert "3." in prompt


# === JSON Parsing Tests ===

class TestResponseParsing:
    """Tests for _parse_response()."""

    def test_parse_clean_json(self, security_validator):
        validator = LLMValidator(security_validator, _make_completion_fn())
        result = validator._parse_response(
            '{"severity": "pass", "justification": "Task is secure."}'
        )
        assert result.severity == "pass"
        assert result.justification == "Task is secure."
        assert result.validator_id == "security"

    def test_parse_blocker(self, security_validator):
        validator = LLMValidator(security_validator, _make_completion_fn())
        result = validator._parse_response(
            '{"severity": "blocker", "justification": "SQL injection risk."}'
        )
        assert result.severity == "blocker"
        assert "SQL injection" in result.justification

    def test_parse_warn(self, security_validator):
        validator = LLMValidator(security_validator, _make_completion_fn())
        result = validator._parse_response(
            '{"severity": "warn", "justification": "Minor concern."}'
        )
        assert result.severity == "warn"

    def test_parse_json_in_code_block(self, security_validator):
        validator = LLMValidator(security_validator, _make_completion_fn())
        result = validator._parse_response(
            '```json\n{"severity": "pass", "justification": "OK"}\n```'
        )
        assert result.severity == "pass"

    def test_parse_json_with_surrounding_text(self, security_validator):
        validator = LLMValidator(security_validator, _make_completion_fn())
        result = validator._parse_response(
            'Here is my evaluation:\n{"severity": "warn", "justification": "Minor issue"}\nDone.'
        )
        assert result.severity == "warn"

    def test_parse_json_with_whitespace(self, security_validator):
        validator = LLMValidator(security_validator, _make_completion_fn())
        result = validator._parse_response(
            '  \n  {"severity": "pass", "justification": "Clean"}  \n  '
        )
        assert result.severity == "pass"

    def test_parse_invalid_json_returns_warn(self, security_validator):
        validator = LLMValidator(security_validator, _make_completion_fn())
        result = validator._parse_response("This is not JSON at all")
        assert result.severity == "warn"
        assert "Could not parse" in result.justification

    def test_parse_invalid_severity_returns_warn(self, security_validator):
        validator = LLMValidator(security_validator, _make_completion_fn())
        result = validator._parse_response(
            '{"severity": "critical", "justification": "Bad stuff"}'
        )
        assert result.severity == "warn"
        assert "Invalid severity" in result.justification

    def test_parse_empty_response_returns_warn(self, security_validator):
        validator = LLMValidator(security_validator, _make_completion_fn())
        result = validator._parse_response("")
        assert result.severity == "warn"

    def test_parse_case_insensitive_severity(self, security_validator):
        validator = LLMValidator(security_validator, _make_completion_fn())
        result = validator._parse_response(
            '{"severity": "PASS", "justification": "OK"}'
        )
        assert result.severity == "pass"

    def test_parse_missing_justification_has_default(self, security_validator):
        validator = LLMValidator(security_validator, _make_completion_fn())
        result = validator._parse_response('{"severity": "pass"}')
        assert result.severity == "pass"
        assert result.justification == "No justification provided"


# === LLM Call Tests ===

class TestEvaluate:
    """Tests for the full evaluate() flow."""

    def test_evaluate_pass(self, security_validator, task):
        completion_fn = _make_completion_fn("pass", "All criteria satisfied")
        validator = LLMValidator(security_validator, completion_fn, model="gpt-4")

        result = validator.evaluate(task)

        assert result.severity == "pass"
        assert result.justification == "All criteria satisfied"
        assert result.validator_id == "security"

        # Verify LLM was called
        completion_fn.assert_called_once()
        call_kwargs = completion_fn.call_args[1]
        assert call_kwargs["model"] == "gpt-4"
        assert call_kwargs["temperature"] == 0.0

    def test_evaluate_blocker(self, security_validator, task):
        completion_fn = _make_completion_fn("blocker", "XSS vulnerability detected")
        validator = LLMValidator(security_validator, completion_fn)

        result = validator.evaluate(task)
        assert result.severity == "blocker"
        assert "XSS" in result.justification

    def test_evaluate_uses_model_from_init(self, security_validator, task):
        completion_fn = _make_completion_fn()
        validator = LLMValidator(
            security_validator, completion_fn, model="claude-sonnet-4-20250514"
        )
        validator.evaluate(task)

        call_kwargs = completion_fn.call_args[1]
        assert call_kwargs["model"] == "claude-sonnet-4-20250514"

    def test_evaluate_sends_prompt_in_messages(self, security_validator, task):
        completion_fn = _make_completion_fn()
        validator = LLMValidator(security_validator, completion_fn)
        validator.evaluate(task)

        call_kwargs = completion_fn.call_args[1]
        messages = call_kwargs["messages"]
        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        assert task.spec in messages[0]["content"]


# === Fallback Tests ===

class TestFallback:
    """Tests for fallback behavior when LLM fails."""

    def test_llm_exception_returns_warn(self, security_validator, task):
        completion_fn = MagicMock(side_effect=Exception("API rate limit"))
        validator = LLMValidator(security_validator, completion_fn)

        result = validator.evaluate(task)

        assert result.severity == "warn"
        assert "LLM validation failed" in result.justification
        assert "API rate limit" in result.justification
        assert result.validator_id == "security"

    def test_llm_timeout_returns_warn(self, security_validator, task):
        completion_fn = MagicMock(side_effect=TimeoutError("Connection timed out"))
        validator = LLMValidator(security_validator, completion_fn)

        result = validator.evaluate(task)
        assert result.severity == "warn"
        assert "timed out" in result.justification

    def test_llm_returns_garbage_returns_warn(self, security_validator, task):
        response = MagicMock()
        response.choices = [MagicMock()]
        response.choices[0].message.content = "I don't understand the question"
        completion_fn = MagicMock(return_value=response)

        validator = LLMValidator(security_validator, completion_fn)
        result = validator.evaluate(task)

        assert result.severity == "warn"
        assert "Could not parse" in result.justification

    def test_never_returns_pass_on_failure(self, security_validator, task):
        """Fallback must be warn, never pass."""
        completion_fn = MagicMock(side_effect=Exception("fail"))
        validator = LLMValidator(security_validator, completion_fn)

        result = validator.evaluate(task)
        assert result.severity != "pass"


# === Engine Loop Integration Tests ===

class TestEngineLoopIntegration:
    """Tests for LLMValidator wiring in the engine loop."""

    def _build_protocol(self):
        """Build a minimal protocol with pre_execute validators."""
        from snodo.compiler.models import Protocol, Mode
        return Protocol(
            protocol_id="test",
            name="Test Protocol",
            version="1.0.0",
            modes=[
                Mode(
                    mode_id="producer",
                    name="Producer",
                    tools=["edit"],
                    validators=["security", "architecture"],
                    transitions={},
                ),
            ],
            validators=[
                Validator(
                    validator_id="security",
                    validator_type="security",
                    evaluation_phase="pre_execute",
                    criteria=["Check for vulnerabilities", "Validate input sanitization"],
                ),
                Validator(
                    validator_id="architecture",
                    validator_type="architecture",
                    evaluation_phase="pre_execute",
                    criteria=["Check design patterns"],
                ),
            ],
            disagreement_policy="unanimous",
            initial_mode="producer",
            global_constraints=[],
        )

    def test_llm_validator_used_when_completion_fn_available(self):
        """When coder has _completion_fn, LLM validators should be used."""
        from snodo.engine.loop import GraphBuilder
        from snodo.coders.mock import MockAdapter

        protocol = self._build_protocol()

        # Create a coder with _completion_fn
        coder = MockAdapter()
        coder._completion_fn = _make_completion_fn("pass", "Security checks pass")

        builder = GraphBuilder(protocol, coder=coder)
        task = Task(id="t1", spec="Build login page")

        validators = [protocol.get_validator("security")]
        results = builder._default_validator(task, validators, None)

        # Should have 1 result from LLM validator (no shell_mcp)
        assert len(results) == 1
        assert results[0].validator_id == "security"
        assert results[0].severity == "pass"
        assert results[0].justification == "Security checks pass"

    def test_warn_when_no_completion_fn_but_has_criteria(self):
        """When coder has no _completion_fn but validator has criteria, should warn."""
        from snodo.engine.loop import GraphBuilder
        from snodo.coders.mock import MockAdapter

        protocol = self._build_protocol()
        coder = MockAdapter()  # No _completion_fn

        builder = GraphBuilder(protocol, coder=coder)
        task = Task(id="t1", spec="Build login page")

        validators = [protocol.get_validator("security")]
        results = builder._default_validator(task, validators, None)

        assert len(results) == 1
        assert results[0].validator_id == "security"
        assert results[0].severity == "warn"
        assert "LLM" in results[0].justification

    def test_stub_used_for_empty_criteria(self):
        """Validators without criteria should use stub even if LLM available."""
        from snodo.engine.loop import GraphBuilder
        from snodo.compiler.models import Protocol, Mode
        from snodo.coders.mock import MockAdapter

        protocol = Protocol(
            protocol_id="test",
            name="Test",
            version="1.0.0",
            modes=[
                Mode(
                    mode_id="producer",
                    name="Producer",
                    tools=["edit"],
                    validators=["empty_val"],
                    transitions={},
                ),
            ],
            validators=[
                Validator(
                    validator_id="empty_val",
                    validator_type="security",
                    evaluation_phase="pre_execute",
                    criteria=[],  # No criteria
                ),
            ],
            disagreement_policy="unanimous",
            initial_mode="producer",
            global_constraints=[],
        )

        coder = MockAdapter()
        coder._completion_fn = _make_completion_fn()

        builder = GraphBuilder(protocol, coder=coder)
        task = Task(id="t1", spec="Task")

        validators = [protocol.get_validator("empty_val")]
        results = builder._default_validator(task, validators, None)

        assert len(results) == 1
        assert results[0].severity == "pass"
        assert "Stub" in results[0].justification

    def test_llm_failure_returns_warn_in_loop(self):
        """LLM failure in loop should return warn, not crash."""
        from snodo.engine.loop import GraphBuilder
        from snodo.coders.mock import MockAdapter

        protocol = self._build_protocol()

        coder = MockAdapter()
        coder._completion_fn = MagicMock(side_effect=Exception("LLM down"))

        builder = GraphBuilder(protocol, coder=coder)
        task = Task(id="t1", spec="Build feature")

        validators = [protocol.get_validator("security")]
        results = builder._default_validator(task, validators, None)

        assert len(results) == 1
        assert results[0].severity == "warn"
        assert "LLM validation failed" in results[0].justification

    def test_multiple_validators_all_evaluated(self):
        """Multiple validators should each get their own LLM call."""
        from snodo.engine.loop import GraphBuilder
        from snodo.coders.mock import MockAdapter

        protocol = self._build_protocol()

        coder = MockAdapter()
        coder._completion_fn = _make_completion_fn("pass", "OK")

        builder = GraphBuilder(protocol, coder=coder)
        task = Task(id="t1", spec="Build feature")

        validators = [
            protocol.get_validator("security"),
            protocol.get_validator("architecture"),
        ]
        results = builder._default_validator(task, validators, None)

        assert len(results) == 2
        assert results[0].validator_id == "security"
        assert results[1].validator_id == "architecture"
        assert coder._completion_fn.call_count == 2

    def test_model_passed_from_coder(self):
        """LLMValidator should use the coder's model."""
        from snodo.engine.loop import GraphBuilder
        from snodo.coders.mock import MockAdapter

        protocol = self._build_protocol()

        coder = MockAdapter()
        coder._completion_fn = _make_completion_fn("pass", "OK")
        coder.model = "claude-sonnet-4-20250514"

        builder = GraphBuilder(protocol, coder=coder)
        task = Task(id="t1", spec="Build feature")

        validators = [protocol.get_validator("security")]
        builder._default_validator(task, validators, None)

        call_kwargs = coder._completion_fn.call_args[1]
        assert call_kwargs["model"] == "claude-sonnet-4-20250514"


# === Import Tests ===

class TestImports:
    """Tests that validator modules import correctly."""

    def test_import_llm_validator(self):
        from snodo.validators.llm_validator import LLMValidator
        assert LLMValidator is not None

    def test_llm_validator_has_evaluate(self):
        from snodo.validators.llm_validator import LLMValidator
        assert hasattr(LLMValidator, "evaluate")
        assert callable(getattr(LLMValidator, "evaluate"))

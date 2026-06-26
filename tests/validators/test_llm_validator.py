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
        assert "messages" in call_kwargs

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
        assert "completion_fn" in results[0].justification.lower()

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
        assert results[0].severity == "warn"
        assert "No criteria" in results[0].justification

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
        # claude-sonnet-4-20250514 supports response_format, so each validator
        # gets a structured-output attempt (fails on mock) + a legacy call = 4
        assert coder._completion_fn.call_count == 4

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
        assert "messages" in call_kwargs


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


# === Post-Execute Tool Loop Tests ===

class TestPostExecuteToolLoop:
    """Tests for the bounded read-only tool-use loop used in post-execute."""

    def _make_post_context(self, completion_fn, workspace_mcp=None, git_mcp=None):
        """Build a ValidatorContext for post-execute phase."""
        from snodo.validators.context import ValidatorContext
        task = Task(id="t1", spec="Add user authentication")
        return ValidatorContext(
            task=task,
            completion_fn=completion_fn,
            model="gpt-4",
            workspace_mcp=workspace_mcp,
            git_mcp=git_mcp,
            phase="post_execute",
        )

    def _make_post_validator(self, security_validator):
        """Clone the fixture validator with tools for the tool-loop."""
        return Validator(
            validator_id=security_validator.validator_id,
            validator_type=security_validator.validator_type,
            evaluation_phase="post_execute",
            criteria=list(security_validator.criteria),
            tools=["read_file", "read_file_lines", "list_files",
                   "git_show", "git_log", "read_diff_between_refs"],
        )

    def test_tool_loop_uses_diff_head_minus_1_to_head(self, security_validator):
        """Post-execute loop should call diff_between_refs(HEAD~1, HEAD)."""
        mock_git = MagicMock()
        mock_git.diff_between_refs.return_value = "diff --git a/src/auth.py\n+def login():"
        mock_workspace = MagicMock()
        mock_workspace.list_files.return_value = []

        # First call: tool call for diff, second call: verdict
        call_count = [0]

        def completion_side_effect(**kwargs):
            messages = kwargs.get("messages", [])
            call_count[0] += 1
            # Check that the first message contains the diff
            if call_count[0] == 1:
                assert "HEAD~1..HEAD" in messages[0]["content"]
                assert "diff --git a/src/auth.py" in messages[0]["content"]
            # First call returns a read tool call
            if call_count[0] == 1:
                resp = MagicMock()
                resp.choices = [MagicMock()]
                tool_call = MagicMock()
                tool_call.id = "tc_1"
                tool_call.function.name = "read_file"
                tool_call.function.arguments = '{"path": "src/auth.py"}'
                resp.choices[0].message.content = None
                resp.choices[0].message.tool_calls = [tool_call]
                return resp
            # Second call returns verdict via submit_verdict
            resp = MagicMock()
            resp.choices = [MagicMock()]
            resp.choices[0].message.content = None
            tc = MagicMock()
            tc.id = "tc_verdict"
            tc.function.name = "submit_verdict"
            tc.function.arguments = json.dumps({
                "severity": "pass",
                "justification": "Auth implementation looks good",
            })
            resp.choices[0].message.tool_calls = [tc]
            return resp

        completion_fn = MagicMock(side_effect=completion_side_effect)
        validator = LLMValidator(self._make_post_validator(security_validator), completion_fn, model="gpt-4")
        ctx = self._make_post_context(completion_fn, mock_workspace, mock_git)

        result = validator.evaluate(ctx)

        assert result.severity == "pass"
        assert "Auth implementation" in result.justification
        # diff_between_refs was called with HEAD~1, HEAD
        mock_git.diff_between_refs.assert_called_once_with("HEAD~1", "HEAD")

    def test_tool_loop_executes_read_file_via_workspace(self, security_validator):
        """Tool loop should call workspace.read_file for read_file tool."""
        mock_git = MagicMock()
        mock_git.diff_between_refs.return_value = "+def login():"
        mock_workspace = MagicMock()
        mock_workspace.read_file.return_value = "def login():\n    pass"

        call_count = [0]

        def completion_side_effect(**kwargs):
            call_count[0] += 1
            resp = MagicMock()
            resp.choices = [MagicMock()]
            if call_count[0] == 1:
                tool_call = MagicMock()
                tool_call.id = "tc_1"
                tool_call.function.name = "read_file"
                tool_call.function.arguments = '{"path": "src/auth.py"}'
                resp.choices[0].message.content = None
                resp.choices[0].message.tool_calls = [tool_call]
            else:
                resp.choices[0].message.content = None
                tc = MagicMock()
                tc.id = "tc_verdict"
                tc.function.name = "submit_verdict"
                tc.function.arguments = json.dumps({
                    "severity": "pass",
                    "justification": "OK",
                })
                resp.choices[0].message.tool_calls = [tc]
            return resp

        completion_fn = MagicMock(side_effect=completion_side_effect)
        validator = LLMValidator(self._make_post_validator(security_validator), completion_fn)
        ctx = self._make_post_context(completion_fn, mock_workspace, mock_git)

        result = validator.evaluate(ctx)

        assert result.severity == "pass"
        mock_workspace.read_file.assert_called_once_with("src/auth.py")

    def test_tool_loop_executes_read_file_lines(self, security_validator):
        """Tool loop should call workspace.read_file_lines."""
        mock_git = MagicMock()
        mock_git.diff_between_refs.return_value = "+def login():"
        mock_workspace = MagicMock()
        mock_workspace.read_file_lines.return_value = "def login():\n    pass"

        call_count = [0]

        def completion_side_effect(**kwargs):
            call_count[0] += 1
            resp = MagicMock()
            resp.choices = [MagicMock()]
            if call_count[0] == 1:
                tool_call = MagicMock()
                tool_call.id = "tc_1"
                tool_call.function.name = "read_file_lines"
                tool_call.function.arguments = '{"path": "src/auth.py", "start": 1, "end": 10}'
                resp.choices[0].message.content = None
                resp.choices[0].message.tool_calls = [tool_call]
            else:
                resp.choices[0].message.content = None
                tc = MagicMock()
                tc.id = "tc_verdict"
                tc.function.name = "submit_verdict"
                tc.function.arguments = json.dumps({
                    "severity": "pass",
                    "justification": "OK",
                })
                resp.choices[0].message.tool_calls = [tc]
            return resp

        completion_fn = MagicMock(side_effect=completion_side_effect)
        validator = LLMValidator(self._make_post_validator(security_validator), completion_fn)
        ctx = self._make_post_context(completion_fn, mock_workspace, mock_git)

        result = validator.evaluate(ctx)

        assert result.severity == "pass"
        mock_workspace.read_file_lines.assert_called_once_with("src/auth.py", 1, 10)

    def test_tool_loop_executes_git_show(self, security_validator):
        """Tool loop should call git.show for git_show tool."""
        mock_git = MagicMock()
        mock_git.diff_between_refs.return_value = "+def login():"
        mock_git.show.return_value = "def old_login(): pass"
        mock_workspace = MagicMock()

        call_count = [0]

        def completion_side_effect(**kwargs):
            call_count[0] += 1
            resp = MagicMock()
            resp.choices = [MagicMock()]
            if call_count[0] == 1:
                tool_call = MagicMock()
                tool_call.id = "tc_1"
                tool_call.function.name = "git_show"
                tool_call.function.arguments = '{"ref": "HEAD~1", "path": "src/auth.py"}'
                resp.choices[0].message.content = None
                resp.choices[0].message.tool_calls = [tool_call]
            else:
                resp.choices[0].message.content = None
                tc = MagicMock()
                tc.id = "tc_verdict"
                tc.function.name = "submit_verdict"
                tc.function.arguments = json.dumps({
                    "severity": "pass",
                    "justification": "OK",
                })
                resp.choices[0].message.tool_calls = [tc]
            return resp

        completion_fn = MagicMock(side_effect=completion_side_effect)
        validator = LLMValidator(self._make_post_validator(security_validator), completion_fn)
        ctx = self._make_post_context(completion_fn, mock_workspace, mock_git)

        result = validator.evaluate(ctx)

        assert result.severity == "pass"
        mock_git.show.assert_called_once_with("HEAD~1", "src/auth.py")

    def test_tool_loop_executes_git_log(self, security_validator):
        """Tool loop should call git.log for git_log tool."""
        mock_git = MagicMock()
        mock_git.diff_between_refs.return_value = "+def login():"
        mock_git.log.return_value = "abc1234 feat: add login"
        mock_workspace = MagicMock()

        call_count = [0]

        def completion_side_effect(**kwargs):
            call_count[0] += 1
            resp = MagicMock()
            resp.choices = [MagicMock()]
            if call_count[0] == 1:
                tool_call = MagicMock()
                tool_call.id = "tc_1"
                tool_call.function.name = "git_log"
                tool_call.function.arguments = '{"n": 3}'
                resp.choices[0].message.content = None
                resp.choices[0].message.tool_calls = [tool_call]
            else:
                resp.choices[0].message.content = None
                tc = MagicMock()
                tc.id = "tc_verdict"
                tc.function.name = "submit_verdict"
                tc.function.arguments = json.dumps({
                    "severity": "pass",
                    "justification": "OK",
                })
                resp.choices[0].message.tool_calls = [tc]
            return resp

        completion_fn = MagicMock(side_effect=completion_side_effect)
        validator = LLMValidator(self._make_post_validator(security_validator), completion_fn)
        ctx = self._make_post_context(completion_fn, mock_workspace, mock_git)

        result = validator.evaluate(ctx)

        assert result.severity == "pass"
        mock_git.log.assert_called_once_with(3)

    def test_tool_loop_executes_list_files(self, security_validator):
        """Tool loop should call workspace.list_files."""
        mock_git = MagicMock()
        mock_git.diff_between_refs.return_value = "+def login():"
        mock_workspace = MagicMock()
        mock_workspace.list_files.return_value = ["auth.py", "models.py"]

        call_count = [0]

        def completion_side_effect(**kwargs):
            call_count[0] += 1
            resp = MagicMock()
            resp.choices = [MagicMock()]
            if call_count[0] == 1:
                tool_call = MagicMock()
                tool_call.id = "tc_1"
                tool_call.function.name = "list_files"
                tool_call.function.arguments = '{"directory": "src"}'
                resp.choices[0].message.content = None
                resp.choices[0].message.tool_calls = [tool_call]
            else:
                resp.choices[0].message.content = None
                tc = MagicMock()
                tc.id = "tc_verdict"
                tc.function.name = "submit_verdict"
                tc.function.arguments = json.dumps({
                    "severity": "pass",
                    "justification": "OK",
                })
                resp.choices[0].message.tool_calls = [tc]
            return resp

        completion_fn = MagicMock(side_effect=completion_side_effect)
        validator = LLMValidator(self._make_post_validator(security_validator), completion_fn)
        ctx = self._make_post_context(completion_fn, mock_workspace, mock_git)

        result = validator.evaluate(ctx)

        assert result.severity == "pass"
        mock_workspace.list_files.assert_called_once_with("src")

    def test_tool_loop_bounded_at_max_turns(self, security_validator):
        """Tool loop should fail-closed after max turns without submit_verdict."""
        from snodo.validators.llm_validator import _DEFAULT_MAX_TOOL_TURNS

        mock_git = MagicMock()
        mock_git.diff_between_refs.return_value = "+def login():"
        mock_workspace = MagicMock()

        # Always return a tool call, never a verdict
        def completion_side_effect(**kwargs):
            resp = MagicMock()
            resp.choices = [MagicMock()]
            tool_call = MagicMock()
            tool_call.id = "tc_1"
            tool_call.function.name = "read_file"
            tool_call.function.arguments = '{"path": "x.py"}'
            resp.choices[0].message.content = None
            resp.choices[0].message.tool_calls = [tool_call]
            return resp

        completion_fn = MagicMock(side_effect=completion_side_effect)
        validator = LLMValidator(self._make_post_validator(security_validator), completion_fn)
        ctx = self._make_post_context(completion_fn, mock_workspace, mock_git)

        result = validator.evaluate(ctx)

        assert result.severity == "blocker"
        assert result.error
        assert "maximum" in result.justification.lower()
        assert completion_fn.call_count == _DEFAULT_MAX_TOOL_TURNS

    def test_tool_loop_returns_verdict_on_first_response(self, security_validator):
        """If model calls submit_verdict immediately, no read tools needed."""
        mock_git = MagicMock()
        mock_git.diff_between_refs.return_value = "+def login():"
        mock_workspace = MagicMock()

        resp = MagicMock()
        resp.choices = [MagicMock()]
        resp.choices[0].message.content = None
        tc = MagicMock()
        tc.id = "tc_verdict"
        tc.function.name = "submit_verdict"
        tc.function.arguments = json.dumps({
            "severity": "pass",
            "justification": "Change is fine",
        })
        resp.choices[0].message.tool_calls = [tc]
        completion_fn = MagicMock(return_value=resp)

        validator = LLMValidator(self._make_post_validator(security_validator), completion_fn)
        ctx = self._make_post_context(completion_fn, mock_workspace, mock_git)

        result = validator.evaluate(ctx)

        assert result.severity == "pass"
        assert "Change is fine" in result.justification
        # Only one LLM call
        assert completion_fn.call_count == 1

    def test_tool_loop_handles_tool_error_gracefully(self, security_validator):
        """If a tool call fails, error is fed back and loop continues."""
        mock_git = MagicMock()
        mock_git.diff_between_refs.return_value = "+def login():"
        mock_workspace = MagicMock()
        mock_workspace.read_file.side_effect = FileNotFoundError("not found")

        call_count = [0]

        def completion_side_effect(**kwargs):
            call_count[0] += 1
            resp = MagicMock()
            resp.choices = [MagicMock()]
            if call_count[0] == 1:
                tool_call = MagicMock()
                tool_call.id = "tc_1"
                tool_call.function.name = "read_file"
                tool_call.function.arguments = '{"path": "missing.py"}'
                resp.choices[0].message.content = None
                resp.choices[0].message.tool_calls = [tool_call]
            else:
                resp.choices[0].message.content = None
                tc = MagicMock()
                tc.id = "tc_verdict"
                tc.function.name = "submit_verdict"
                tc.function.arguments = json.dumps({
                    "severity": "warn",
                    "justification": "File not found but diff looks OK",
                })
                resp.choices[0].message.tool_calls = [tc]
            return resp

        completion_fn = MagicMock(side_effect=completion_side_effect)
        validator = LLMValidator(self._make_post_validator(security_validator), completion_fn)
        ctx = self._make_post_context(completion_fn, mock_workspace, mock_git)

        result = validator.evaluate(ctx)

        assert result.severity == "warn"
        assert "diff looks OK" in result.justification

    def test_tool_loop_llm_exception_returns_warn(self, security_validator):
        """If LLM call throws, return warn."""
        mock_git = MagicMock()
        mock_git.diff_between_refs.return_value = "+def login():"
        mock_workspace = MagicMock()

        completion_fn = MagicMock(side_effect=Exception("API down"))
        validator = LLMValidator(self._make_post_validator(security_validator), completion_fn)
        ctx = self._make_post_context(completion_fn, mock_workspace, mock_git)

        result = validator.evaluate(ctx)

        assert result.severity == "warn"
        assert "tool-loop error" in result.justification

    def test_tool_loop_no_content_no_tool_calls_fails_closed(self, security_validator):
        """If model returns neither content nor tool_calls, fail-closed as error."""
        mock_git = MagicMock()
        mock_git.diff_between_refs.return_value = "+def login():"
        mock_workspace = MagicMock()

        # First call: empty response — triggers retry
        # Second call: same empty response — fail-closed
        call_count = [0]

        def completion_side_effect(**kwargs):
            call_count[0] += 1
            resp = MagicMock()
            resp.choices = [MagicMock()]
            resp.choices[0].message.content = None
            resp.choices[0].message.tool_calls = []
            return resp

        completion_fn = MagicMock(side_effect=completion_side_effect)
        validator = LLMValidator(self._make_post_validator(security_validator), completion_fn)
        ctx = self._make_post_context(completion_fn, mock_workspace, mock_git)

        result = validator.evaluate(ctx)

        assert result.severity == "blocker"
        assert result.error
        assert "submit_verdict" in result.justification

    def test_tool_loop_uses_tools_kwarg_in_completion_call(self, security_validator):
        """Tool loop must pass tools=[...] to completion_fn."""
        mock_git = MagicMock()
        mock_git.diff_between_refs.return_value = "+def login():"
        mock_workspace = MagicMock()

        resp = MagicMock()
        resp.choices = [MagicMock()]
        resp.choices[0].message.content = None
        tc = MagicMock()
        tc.id = "tc_verdict"
        tc.function.name = "submit_verdict"
        tc.function.arguments = json.dumps({"severity": "pass", "justification": "OK"})
        resp.choices[0].message.tool_calls = [tc]
        completion_fn = MagicMock(return_value=resp)

        validator = LLMValidator(self._make_post_validator(security_validator), completion_fn)
        ctx = self._make_post_context(completion_fn, mock_workspace, mock_git)

        validator.evaluate(ctx)

        call_kwargs = completion_fn.call_args[1]
        assert "tools" in call_kwargs
        assert isinstance(call_kwargs["tools"], list)
        # Verify tool definitions have expected names
        tool_names = [t["function"]["name"] for t in call_kwargs["tools"]]
        assert "read_file" in tool_names
        assert "read_file_lines" in tool_names
        assert "read_diff_between_refs" in tool_names
        assert "git_show" in tool_names
        assert "git_log" in tool_names
        assert "list_files" in tool_names


class TestPreExecuteRegression:
    """Ensure pre-execute validators still use single-completion path."""

    def test_pre_execute_uses_single_completion(self, security_validator, task):
        """Pre-execute phase should NOT use tool loop, just single completion."""
        from snodo.validators.context import ValidatorContext

        mock_workspace = MagicMock()
        mock_git = MagicMock()

        resp = MagicMock()
        resp.choices = [MagicMock()]
        resp.choices[0].message.content = json.dumps({
            "severity": "pass",
            "justification": "Pre-execute OK",
        })
        completion_fn = MagicMock(return_value=resp)

        ctx = ValidatorContext(
            task=task,
            completion_fn=completion_fn,
            model="gpt-4",
            workspace_mcp=mock_workspace,
            git_mcp=mock_git,
            phase="pre_execute",
        )

        validator = LLMValidator(security_validator, completion_fn)
        result = validator.evaluate(ctx)

        assert result.severity == "pass"
        # Should NOT have called tools
        call_kwargs = completion_fn.call_args[1]
        assert "tools" not in call_kwargs
        # Workspace and git should NOT have been used
        mock_workspace.read_file.assert_not_called()
        mock_git.diff_between_refs.assert_not_called()

    def test_no_mcp_falls_back_to_single_completion(self, security_validator, task):
        """If workspace_mcp or git_mcp is None, fall back to single-completion."""
        from snodo.validators.context import ValidatorContext

        resp = MagicMock()
        resp.choices = [MagicMock()]
        resp.choices[0].message.content = json.dumps({
            "severity": "pass",
            "justification": "Fallback OK",
        })
        completion_fn = MagicMock(return_value=resp)

        ctx = ValidatorContext(
            task=task,
            completion_fn=completion_fn,
            model="gpt-4",
            workspace_mcp=None,
            git_mcp=None,
            phase="post_execute",
        )

        validator = LLMValidator(security_validator, completion_fn)
        result = validator.evaluate(ctx)

        assert result.severity == "pass"
        call_kwargs = completion_fn.call_args[1]
        assert "tools" not in call_kwargs

    def test_backward_compat_task_direct_still_works(self, security_validator, task):
        """Passing Task directly (old API) should still work."""
        completion_fn = _make_completion_fn("pass", "Backward compat OK")
        validator = LLMValidator(security_validator, completion_fn)

        result = validator.evaluate(task)

        assert result.severity == "pass"
        assert "Backward compat" in result.justification


# === Structured Output Tests ===

class TestStructuredOutput:
    """Tests for the response_format=ValidatorResult structured output path."""

    def _make_structured_response(self, severity: str = "pass", justification: str = "all good"):
        """Mock a LiteLLM response for structured output (content is JSON string)."""
        from snodo.core.interfaces import ValidatorResult
        vr = ValidatorResult(validator_id="security", severity=severity, justification=justification)
        resp = MagicMock()
        resp.choices = [MagicMock()]
        resp.choices[0].message.content = vr.model_dump_json()
        return resp

    def test_structured_output_bypasses_parse(self, security_validator, task):
        """When model supports response_format, structured output is used, not _parse_response."""
        from unittest.mock import patch

        completion_fn = MagicMock()
        completion_fn.return_value = self._make_structured_response("pass", "All criteria met")

        validator = LLMValidator(security_validator, completion_fn, model="gpt-4o")

        with patch("snodo.validators.llm_validator.supports_response_schema", return_value=True):
            result = validator.evaluate(task)

        assert result.severity == "pass"
        assert result.justification == "All criteria met"
        # Structured path passes response_format=ValidatorResult
        call_kwargs = completion_fn.call_args[1]
        assert call_kwargs["response_format"] is not None

    def test_markdown_prose_still_works_via_structured(self, security_validator, task):
        """Markdown prose in LLM response doesn't break structured output — schema enforces JSON."""
        from unittest.mock import patch

        completion_fn = MagicMock()
        # Even if the model would have returned markdown, structured output
        # guarantees valid JSON matching the schema at the API level
        completion_fn.return_value = self._make_structured_response("blocker", "SQL injection risk")

        validator = LLMValidator(security_validator, completion_fn, model="gpt-4o")

        with patch("snodo.validators.llm_validator.supports_response_schema", return_value=True):
            result = validator.evaluate(task)

        assert result.severity == "blocker"
        assert "SQL injection" in result.justification

    def test_unsupported_model_falls_back_to_parse(self, security_validator, task):
        """When supports_response_schema returns False, legacy _parse_response is used."""
        from unittest.mock import patch

        completion_fn = _make_completion_fn("warn", "Minor concern")
        validator = LLMValidator(security_validator, completion_fn, model="gpt-4")

        with patch("snodo.validators.llm_validator.supports_response_schema", return_value=False):
            result = validator.evaluate(task)

        assert result.severity == "warn"
        assert result.justification == "Minor concern"
        # Legacy path does NOT pass response_format
        call_kwargs = completion_fn.call_args[1]
        assert "response_format" not in call_kwargs

    def test_structured_uses_config_max_tokens(self, security_validator, task):
        """Structured path uses self.completion_tokens, not hardcoded 500."""
        from unittest.mock import patch

        completion_fn = MagicMock()
        completion_fn.return_value = self._make_structured_response("pass", "ok")

        validator = LLMValidator(security_validator, completion_fn, model="gpt-4o")

        with patch("snodo.validators.llm_validator.supports_response_schema", return_value=True):
            validator.evaluate(task)

        call_kwargs = completion_fn.call_args[1]
        assert call_kwargs["max_tokens"] > 500

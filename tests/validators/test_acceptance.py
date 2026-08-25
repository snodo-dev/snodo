"""Tests for the acceptance validator (Fixes #54).

The acceptance validator judges the produced artifacts against the task's
acceptance criteria.  It must:
- distinguish "unmet" (verifiable from the tree, demonstrably absent) from
  "uncheckable" (device behaviour, human judgement — never a finding);
- warn, not block, on a miss (severity_cap: warn in the shipped templates);
- not become a second `quality` — it judges completeness against the spec,
  never correctness of the code.
"""

import json
from unittest.mock import MagicMock

from snodo.compiler.models import Validator
from snodo.core.interfaces import Task, ValidatorResult
from snodo.validators.acceptance import AcceptanceValidator
from snodo.validators.context import ValidatorContext
from snodo.validators.registry import _default_registry


def _make_response(content=None, tool_calls=None):
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = content
    resp.choices[0].message.tool_calls = tool_calls or []
    resp.choices[0].finish_reason = "tool_calls"
    return resp


def _verdict_call(severity, justification):
    tc = MagicMock()
    tc.id = "call_verdict"
    tc.function.name = "submit_verdict"
    tc.function.arguments = json.dumps({
        "severity": severity,
        "justification": justification,
    })
    return tc


def _read_call(path="src/main.py"):
    tc = MagicMock()
    tc.id = "call_read"
    tc.function.name = "read_file"
    tc.function.arguments = json.dumps({"path": path})
    return tc


def _validator():
    return Validator(
        validator_id="acceptance",
        validator_type="acceptance",
        evaluation_phase="post_execute",
        severity_cap="warn",
        tools=["read_file", "list_files", "read_diff_between_refs"],
        criteria=["Judge the produced artifacts against the acceptance criteria"],
    )


def _context(completion_fn, artifacts=None, workspace=None, git=None):
    return ValidatorContext(
        task=Task(id="t1", spec=(
            "Add a login endpoint.\n"
            "Acceptance criteria:\n"
            "1. A test covers the new endpoint.\n"
            "2. The endpoint rejects invalid tokens.\n"
            "3. The feature is documented in docs/decisions/."
        )),
        completion_fn=completion_fn,
        workspace_mcp=workspace or MagicMock(),
        git_mcp=git or MagicMock(),
        phase="post_execute",
        max_tool_turns=5,
        artifacts=artifacts or [],
    )


class TestAcceptanceValidatorRegistered:
    def test_registered_type(self):
        assert AcceptanceValidator.registered_type() == "acceptance"

    def test_registry_contains_acceptance(self):
        assert _default_registry.lookup("acceptance") is AcceptanceValidator


class TestAcceptanceValidatorPrompt:
    def test_prompt_contains_artifacts_and_acceptance_instructions(self):
        completion_fn = MagicMock(return_value=_make_response(
            tool_calls=[_verdict_call("pass", "all met")],
        ))
        validator = AcceptanceValidator(_validator())
        ctx = _context(completion_fn, artifacts=["src/main.py", "tests/test_main.py"])

        validator.evaluate(ctx)

        prompt = completion_fn.call_args[1]["messages"][0]["content"]
        assert "src/main.py" in prompt
        assert "tests/test_main.py" in prompt
        assert "Acceptance criteria" in prompt
        assert "UNMET" in prompt
        assert "UNCHECKABLE" in prompt
        assert "NEVER a finding" in prompt

    def test_prompt_notes_when_no_artifacts(self):
        completion_fn = MagicMock(return_value=_make_response(
            tool_calls=[_verdict_call("pass", "no criteria")],
        ))
        validator = AcceptanceValidator(_validator())
        ctx = _context(completion_fn, artifacts=[])

        validator.evaluate(ctx)

        prompt = completion_fn.call_args[1]["messages"][0]["content"]
        assert "(none)" in prompt


class TestAcceptanceValidatorVerdicts:
    def test_pass_when_all_met(self):
        completion_fn = MagicMock(return_value=_make_response(
            tool_calls=[_verdict_call("pass", "all acceptance criteria met")],
        ))
        validator = AcceptanceValidator(_validator())
        result = validator.evaluate(_context(completion_fn, artifacts=["src/main.py"]))

        assert result.severity == "pass"

    def test_warn_when_criterion_unmet(self):
        completion_fn = MagicMock(return_value=_make_response(
            tool_calls=[_verdict_call("warn", "criterion 1 unmet: no test covers the endpoint")],
        ))
        validator = AcceptanceValidator(_validator())
        result = validator.evaluate(_context(completion_fn, artifacts=["src/main.py"]))

        assert result.severity == "warn"

    def test_uncheckable_criterion_is_not_a_finding(self):
        """A criterion that cannot be verified from the tree must not block."""
        completion_fn = MagicMock(return_value=_make_response(
            tool_calls=[_verdict_call("pass", "criterion 3 is uncheckable from the tree; others met")],
        ))
        validator = AcceptanceValidator(_validator())
        result = validator.evaluate(_context(completion_fn, artifacts=["src/main.py"]))

        assert result.severity == "pass"

    def test_tool_loop_reads_files_before_verdict(self):
        workspace = MagicMock()
        workspace.read_file.return_value = "def login(): pass"
        completion_fn = MagicMock(side_effect=[
            _make_response(tool_calls=[_read_call()]),
            _make_response(tool_calls=[_verdict_call("pass", "all met")]),
        ])
        validator = AcceptanceValidator(_validator())
        result = validator.evaluate(_context(
            completion_fn, artifacts=["src/main.py"], workspace=workspace,
        ))

        assert result.severity == "pass"
        workspace.read_file.assert_called_once_with("src/main.py")

    def test_severity_cap_keeps_miss_at_warn(self):
        """Even if the judge returns blocker, the shipped severity_cap=warn
        keeps a miss recoverable rather than a hard halt."""
        completion_fn = MagicMock(return_value=_make_response(
            tool_calls=[_verdict_call("blocker", "criterion 1 unmet")],
        ))
        validator = AcceptanceValidator(_validator())
        result = validator.evaluate(_context(completion_fn, artifacts=["src/main.py"]))

        # The validator itself reports what the judge said; the cap is applied
        # by the shared runner (run_validators), which is what the engine uses.
        assert result.severity == "blocker"

    def test_runner_caps_acceptance_blocker_to_warn(self):
        """The shared runner applies severity_cap=warn to the acceptance
        validator, so a miss routes to recovery, not a hard halt."""
        from snodo.validators.runner import run_validators

        completion_fn = MagicMock(return_value=_make_response(
            tool_calls=[_verdict_call("blocker", "criterion 1 unmet")],
        ))
        protocol = MagicMock()
        protocol.get_mode.return_value = MagicMock(name="producer", tools=[], transitions={}, validators=[])
        protocol.get_validator.return_value = _validator()

        results, _ = run_validators(
            protocol=protocol,
            validators=[_validator()],
            task=Task(id="t1", spec="Add a login endpoint."),
            phase="post_execute",
            completion_fn=completion_fn,
            default_model="gpt-4",
            validator_config=MagicMock(max_tokens=1500, max_tool_turns=6),
            workspace_mcp=MagicMock(),
            git_mcp=MagicMock(),
            current_mode="producer",
            artifacts=["src/main.py"],
        )

        assert results[0].severity == "warn"

    def test_no_acceptance_criteria_returns_pass(self):
        completion_fn = MagicMock(return_value=_make_response(
            tool_calls=[_verdict_call("pass", "no acceptance criteria in spec")],
        ))
        validator = AcceptanceValidator(_validator())
        ctx = _context(completion_fn, artifacts=["src/main.py"])
        ctx.task = Task(id="t1", spec="Add a login endpoint.")

        result = validator.evaluate(ctx)

        assert result.severity == "pass"


class TestAcceptanceValidatorNotQuality:
    def test_prompt_never_mentions_running_tests(self):
        """It judges completeness against the spec, not correctness of code."""
        completion_fn = MagicMock(return_value=_make_response(
            tool_calls=[_verdict_call("pass", "all met")],
        ))
        validator = AcceptanceValidator(_validator())
        ctx = _context(completion_fn, artifacts=["src/main.py"])

        validator.evaluate(ctx)

        prompt = completion_fn.call_args[1]["messages"][0]["content"]
        assert "test command" not in prompt.lower()
        assert "pytest" not in prompt.lower()
        assert "npm test" not in prompt.lower()


class TestArtifactsThreadedToPostValidate:
    """The produced artifacts reach the validator context at post-execute."""

    def test_post_validate_passes_artifacts_to_validator(self):
        from snodo.compiler.models import Protocol, Mode
        from snodo.engine.loop import GraphBuilder

        protocol = Protocol(
            protocol_id="p",
            name="P",
            version="1.0.0",
            initial_mode="producer",
            modes=[Mode(mode_id="producer", name="Producer", tools=["edit"],
                        validators=["acceptance"])],
            validators=[_validator()],
        )

        seen = {}

        def tracking_validator(task, validators, shell_mcp, current_mode="", **kwargs):
            seen["artifacts"] = kwargs.get("artifacts")
            return [
                ValidatorResult(validator_id=v.validator_id, severity="pass", justification="stub")
                for v in validators
            ]

        builder = GraphBuilder(protocol, validator_fn=tracking_validator)

        state = {
            "task": {"id": "t1", "spec": "Add a login endpoint."},
            "current_mode": "producer",
            "iteration": 1,
            "stage": "execute",
            "validation_results": [],
            "validation_token": None,
            "artifacts": ["src/main.py", "tests/test_main.py"],
            "constraints_passed": True,
            "constraint_violations": [],
            "policy_decision": None,
            "is_complete": False,
            "is_blocked": False,
            "metadata": {},
        }

        builder._post_validate_node(state)

        assert seen["artifacts"] == ["src/main.py", "tests/test_main.py"]

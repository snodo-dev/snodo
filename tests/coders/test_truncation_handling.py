"""Tests for truncation failure handling (Issue #39).

FILE: tests/coders/test_truncation_handling.py

Verifies:
- Truncation produces internal_error outcome, zero artifacts, and skipped post-validation.
- The halt payload reason names the max_tokens ceiling and states task is too large.
- Generated token/char counts are reported in the failure reason.
- A complete (non-truncated) response is unaffected.

The graph-execution test runs against a throwaway git fixture repository, never
the repository the test suite runs in: the executor creates and checks out a
task branch, which must not move the suite's own HEAD.
"""

import subprocess
from unittest.mock import MagicMock

import pytest
from snodo.coders.base import ParseError
from snodo.coders.litellm import LiteLLMAdapter
from snodo.compiler.models import Mode, Protocol, Validator
from snodo.core.interfaces import Task
from snodo.engine.closure import run_to_closure
from snodo.engine.loop import build_protocol_graph


@pytest.fixture
def git_fixture_repo(tmp_path):
    """A throwaway git repo with an initial commit, for graph execution.

    The executor creates and checks out a task branch; that must happen in a
    fixture repo, never in the repository the test suite runs in.
    """
    root = tmp_path / "fixture"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    (root / "README.md").write_text("init\n")
    subprocess.run(["git", "add", "README.md"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=root, check=True)
    return root


@pytest.fixture
def solo_protocol():
    return Protocol(
        protocol_id="test_truncation",
        name="Test Truncation Protocol",
        version="1.0.0",
        modes=[
            Mode(
                mode_id="producer",
                name="Producer",
                tools=["edit"],
                validators=["spec_check", "quality"],
            )
        ],
        validators=[
            Validator(
                validator_id="spec_check",
                validator_type="llm",
                criteria=["Spec is clear"],
            ),
            Validator(
                validator_id="quality",
                validator_type="quality",
                criteria=["Code must compile"],
            ),
        ],
        initial_mode="producer",
    )


def _make_mock_response(content="Preamble text...", finish_reason="max_tokens", tokens=16000):
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = content
    response.choices[0].message.tool_calls = []
    response.choices[0].finish_reason = finish_reason
    usage = MagicMock()
    usage.completion_tokens = tokens
    response.usage = usage
    return response


class TestTruncationExecutionFailure:
    """Issue #39: Truncation is an execution failure with outcome internal_error."""

    def test_truncated_response_produces_internal_error(self, solo_protocol, git_fixture_repo):
        """Truncated response -> internal_error, zero artifacts, skipped post-validation."""
        adapter = LiteLLMAdapter(model="gpt-4o", max_tokens=16000)
        adapter._completion_fn = MagicMock(return_value=_make_mock_response(
            content="I am starting to write the preamble...",
            finish_reason="max_tokens",
            tokens=16000,
        ))

        graph = build_protocol_graph(
            protocol=solo_protocol,
            project_root=str(git_fixture_repo),
            use_mock_coder=False,
            coder=adapter,
            validator_fn=lambda task, validators, shell, **kwargs: [
                MagicMock(validator_id=v.validator_id, severity="pass", justification="ok", error=False)
                for v in validators
            ],
        ).compile()

        task = Task(id="task_trunc", spec="Huge task")
        _final_state, tree = run_to_closure(graph, task, mode="producer")

        assert tree.outcome == "internal_error"
        payload = tree.halt_payload
        assert payload is not None

        assert payload["status"] == "blocked"
        assert payload["halt_type"] == "internal_error"
        assert payload["final_decision"] == "internal_error"
        assert payload["raw_halt_type"] == "internal_error"
        assert payload["artifacts_count"] == 0

        # Post-validation was skipped
        assert payload["post_validation"] is not None
        assert payload["post_validation"]["outcome"] == "skipped"

        # Reason names the token ceiling (16000) and says task is too large
        reason = payload["reason"]
        assert "max_tokens=16000" in reason
        assert "task is too large" in reason
        assert "16000 tokens" in reason
        assert "38 chars" in reason

    def test_truncation_reasons_coverage(self):
        """All truncation finish reasons ('length', 'max_tokens', 'MAX_TOKENS') trigger ParseError."""
        for finish_reason in ("length", "max_tokens", "MAX_TOKENS"):
            adapter = LiteLLMAdapter(model="gpt-4o", max_tokens=4000)
            response = _make_mock_response("Some output", finish_reason=finish_reason, tokens=4000)
            with pytest.raises(ParseError) as exc_info:
                adapter._check_truncation(response)
            err = str(exc_info.value)
            assert "max_tokens=4000" in err
            assert "task is too large" in err

    def test_complete_response_unaffected(self, solo_protocol):
        """A complete (non-truncated) response is unaffected."""
        adapter = LiteLLMAdapter(model="gpt-4o", max_tokens=16000)
        response = _make_mock_response("Clean content", finish_reason="stop", tokens=100)
        # _check_truncation does not raise
        adapter._check_truncation(response)

from unittest.mock import MagicMock, patch
import json

from snodo.core.interfaces import CodeArtifact
from snodo.coders.litellm import LiteLLMAdapter, _is_test_governing_file
from snodo.tools.workspace import WorkspaceMCP
from snodo.validators.acceptance import AcceptanceValidator
from snodo.compiler.models import Validator
from snodo.validators.context import ValidatorContext, Task


def test_is_test_governing_file():
    """Verify test-governing file detection logic (ADR 040 Point 5)."""
    assert _is_test_governing_file("tests/test_auth.py") is True
    assert _is_test_governing_file("src/utils_test.py") is True
    assert _is_test_governing_file("spec/foo.spec.ts") is True
    assert _is_test_governing_file("conftest.py") is True
    assert _is_test_governing_file("pytest.ini") is True
    assert _is_test_governing_file("pyproject.toml") is True
    assert _is_test_governing_file("package.json") is True
    assert _is_test_governing_file("Cargo.toml") is True

    assert _is_test_governing_file("src/main.py") is False
    assert _is_test_governing_file("README.md") is False


def test_litellm_adapter_observes_tests_capability():
    """LiteLLMAdapter declares observes_tests capability (ADR 040 Point 9)."""
    adapter = LiteLLMAdapter()
    assert adapter.observes_tests is True


def test_workspace_search_string(tmp_path):
    """Test search_string workspace method."""
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "main.py").write_text("def hello():\n    print('HELLO WORLD')\n")
    (proj / "other.py").write_text("print('HELLO AGAIN')\n")

    ws = WorkspaceMCP(str(proj))
    res = ws.search_string("HELLO")

    assert "main.py:" in res
    assert "other.py:" in res
    assert "HELLO WORLD" in res


def test_workspace_search_symbol(tmp_path):
    """Test search_symbol workspace method."""
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "auth.py").write_text("class AuthManager:\n    def authenticate(self):\n        pass\n")

    ws = WorkspaceMCP(str(proj))
    res = ws.search_symbol("AuthManager")

    assert "auth.py:1: class AuthManager:" in res


def test_tool_loop_run_tests(tmp_path):
    """Test run_tests tool execution in LiteLLMAdapter (ADR 040 Point 1, 2)."""
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "tests").mkdir()
    (proj / "tests" / "test_foo.py").write_text("def test_ok(): assert True\n")

    ws = WorkspaceMCP(str(proj))
    adapter = LiteLLMAdapter(workspace_mcp=ws)

    mock_run = MagicMock(return_value=MagicMock(returncode=0, stdout="1 passed", stderr=""))
    with patch("subprocess.run", mock_run):
        output = adapter._execute_coder_run_tests("tests/", "pytest", ws, 1)

    assert "Test Execution Result (exit code: 0)" in output
    assert "1 passed" in output


def test_submit_files_attaches_test_governing_mutations(tmp_path):
    """Submitting test-governing file edits attaches metadata and emits test_modified audit event."""
    proj = tmp_path / "proj"
    proj.mkdir()

    ws = WorkspaceMCP(str(proj))
    adapter = LiteLLMAdapter(workspace_mcp=ws)

    tc = MagicMock()
    tc.id = "tc_sub"
    tc.function.name = "submit_files"
    tc.function.arguments = json.dumps({
        "files": [
            {"path": "tests/test_main.py", "content": "def test_1(): pass", "action": "write"},
            {"path": "src/main.py", "content": "print(1)", "action": "write"}
        ]
    })

    resp1 = MagicMock()
    resp1.choices = [MagicMock()]
    resp1.choices[0].message.content = None
    resp1.choices[0].message.tool_calls = [tc]
    resp1.choices[0].finish_reason = "tool_calls"

    resp2 = MagicMock()
    resp2.choices = [MagicMock()]
    resp2.choices[0].message.content = "Done"
    resp2.choices[0].message.tool_calls = None
    resp2.choices[0].finish_reason = "stop"

    adapter._completion_fn = MagicMock(side_effect=[resp1, resp2])
    raw = adapter._call_llm_with_tools("prompt")
    artifact = adapter._parse_response(raw)

    assert artifact.metadata.get("test_mutation_detected") is True
    mutations = artifact.metadata.get("test_governing_mutations", [])
    assert len(mutations) == 1
    assert mutations[0]["path"] == "tests/test_main.py"
    assert mutations[0]["kind"] == "write"


def test_acceptance_validator_prompt_includes_test_governing_mutations():
    """AcceptanceValidator prompt highlights test-governing file modifications (ADR 040 Point 6)."""
    val_spec = Validator(validator_id="acc", validator_type="acceptance", criteria=["Ensure feature"])
    validator = AcceptanceValidator(validator_spec=val_spec)

    artifact = CodeArtifact(
        files=[],
        metadata={
            "test_mutation_detected": True,
            "test_governing_mutations": [
                {"path": "tests/test_auth.py", "kind": "modified"},
                {"path": "conftest.py", "kind": "deleted"}
            ]
        }
    )

    ctx = ValidatorContext(
        task=Task(id="t1", spec="Add feature"),
        workspace_mcp=MagicMock(),
        code_artifact=artifact,
    )

    prompt = validator._build_tool_loop_prompt(ctx, active_names=set())

    assert "## Test-Governing File Modifications Detected (ADR 040)" in prompt
    assert "tests/test_auth.py (modified)" in prompt
    assert "conftest.py (deleted)" in prompt
    assert "unauthorized test weakening" in prompt


def test_model_tool_result_bound_keeps_omission_notice_and_test_tail():
    from snodo.coders.tool_result_text import MAX_TOOL_RESULT_CHARS, bound_tool_result

    large = "head\n" + ("x" * (MAX_TOOL_RESULT_CHARS + 100)) + "\nFAILED test_example.py::test_failure"
    bounded = bound_tool_result(large, preserve_tail=True)

    assert len(bounded) <= MAX_TOOL_RESULT_CHARS
    assert "TRUNCATED" in bounded
    assert "characters omitted" in bounded
    assert "FAILED test_example.py::test_failure" in bounded


def test_model_tool_result_within_bound_is_unchanged():
    from snodo.coders.tool_result_text import bound_tool_result

    result = "short tool output"
    assert bound_tool_result(result) == result


def test_large_test_result_is_bounded_with_exit_code_and_tail():
    from snodo.coders.tool_result_text import (
        MAX_TOOL_RESULT_CHARS,
        bound_tool_result,
        provider_tool_result_text,
    )

    result = (
        "Test Execution Result (exit code: 1):\n"
        "--- STDOUT ---\n" + "noise\n" * 400_000 +
        "\n--- STDERR ---\nAssertionError: expected value\n"
        "FAILED tests/test_example.py::test_failure"
    )
    # This is the composition used for message content sent to the provider:
    # frame first, then bound, so framing cannot push the final value over cap.
    model_result = bound_tool_result(
        provider_tool_result_text(result), preserve_tail=True,
    )

    assert len(model_result) <= MAX_TOOL_RESULT_CHARS
    assert "exit code: 1" in model_result
    assert "TRUNCATED" in model_result
    assert "characters omitted" in model_result
    assert "run a narrower test target" in model_result
    assert "FAILED tests/test_example.py::test_failure" in model_result


def test_provider_framing_cannot_push_at_limit_result_over_bound():
    from snodo.coders.tool_result_text import (
        MAX_TOOL_RESULT_CHARS,
        bound_tool_result,
        provider_tool_result_text,
    )

    # JSON-shaped tool output receives a provider-facing prefix. The bounded
    # value delivered to the model must include that prefix in its accounting.
    result = "{" + ("x" * (MAX_TOOL_RESULT_CHARS - 2)) + "}"
    framed = provider_tool_result_text(result)
    model_result = bound_tool_result(framed)

    assert len(result) == MAX_TOOL_RESULT_CHARS
    assert len(model_result) <= MAX_TOOL_RESULT_CHARS
    assert model_result.startswith("Tool result (text):\n{")
    assert "TRUNCATED" in model_result
    assert "characters omitted" in model_result
    assert "run a narrower test target" in model_result

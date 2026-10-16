"""Mock coder adapter for testing.

FILE: snodo/coders/mock.py (Fixes #12, #185, #187)

Returns deterministic outputs without making LLM calls.

The mock exists to exercise the engine loop deterministically. Its output
must satisfy the validators, including the quality validator's test runner.
Language selection follows TaskSpec.project_context["language"].

Mock mode is process-wide because engine-created completion functions and
adapters must agree about whether a run is hermetic. The completion double
speaks the same tool contracts as a real model; it must not bypass validation
just because no network request is made.
"""

import json
from pathlib import Path
from typing import Any, Optional
from unittest.mock import MagicMock

from snodo.paths import is_protected_workspace_path
from snodo.core.interfaces import TaskSpec, CodeArtifact, FileArtifact
from snodo.coders.base import CoderAdapter
from snodo.infrastructure.config import DEFAULT_MODEL

_GLOBAL_MOCK_MODE: bool = False


def set_mock_mode(enabled: bool) -> None:
    """Enable or disable global hermetic mock mode for the process."""
    global _GLOBAL_MOCK_MODE
    _GLOBAL_MOCK_MODE = enabled


def is_mock_mode_active() -> bool:
    """Return True if global mock mode is enabled."""
    return _GLOBAL_MOCK_MODE


class MockChoiceMessage:
    """The message-shaped portion of a completion consumed by the engine."""

    def __init__(self, content: str = "", tool_calls: Optional[list] = None):
        self.content = content
        self.tool_calls = tool_calls or []


class MockChoice:
    """A single deterministic choice, with the provider's finish-reason shape."""

    def __init__(self, message: MockChoiceMessage, finish_reason: str = "stop"):
        self.message = message
        self.finish_reason = finish_reason


class MockModelResponse:
    """Minimal model response shared by tool-loop and free-text consumers."""

    def __init__(self, content: str = "", tool_calls: Optional[list] = None, finish_reason: str = "stop"):
        self.choices = [MockChoice(MockChoiceMessage(content, tool_calls), finish_reason)]


def mock_completion_fn(*args: Any, **kwargs: Any) -> MockModelResponse:
    """Hermetic mock completion function. Never touches network or credentials.

    Tool contracts take precedence over prompt heuristics: a judge must submit
    a verdict and a tool-based coder must submit files. Only requests without
    those tools use the legacy classifier/validator/spec response shapes.
    """
    messages = kwargs.get("messages", [])
    tools = kwargs.get("tools", [])
    tool_names = set()
    if isinstance(tools, list):
        for t in tools:
            if isinstance(t, dict):
                fn = t.get("function", {})
                if isinstance(fn, dict) and "name" in fn:
                    tool_names.add(fn["name"])

    if "submit_verdict" in tool_names:
        # Echo the subject from the same prompt a real judge sees, rather
        # than inventing a fixed identifier or borrowing another task's ID.
        # JSON decoding preserves punctuation and escaping in task identifiers.
        marker = "The task identifier under judgement is "
        task_id = ""
        for message in messages:
            content = message.get("content", "") if isinstance(message, dict) else ""
            if isinstance(content, str) and marker in content:
                task_id, _ = json.JSONDecoder().raw_decode(content.rsplit(marker, 1)[1])
                break
        tc = MagicMock()
        tc.id = "mock_val_call_1"
        tc.function.name = "submit_verdict"
        tc.function.arguments = json.dumps({
            "task_id": task_id, "severity": "pass", "justification": "Mock validation pass",
        })
        return MockModelResponse(content="", tool_calls=[tc])

    if "submit_files" in tool_names:
        # The tool-loop path needs a submission, not free-text JSON. The
        # adapter below supplies runnable fixture artifacts for engine tests.
        tc = MagicMock()
        tc.id = "mock_coder_call_1"
        tc.function.name = "submit_files"
        tc.function.arguments = json.dumps({"files": []})
        return MockModelResponse(content="", tool_calls=[tc])

    user_prompt = ""
    for msg in reversed(messages):
        if isinstance(msg, dict) and msg.get("role") == "user":
            user_prompt = str(msg.get("content", ""))
            break
    role_meta = str(kwargs.get("metadata", {}).get("role", ""))
    if "wave" in user_prompt.lower() or "classifier" in role_meta.lower():
        content = json.dumps({
            "flow_type": "feature",
            "wave_id": "W-MOCK-1",
            "task_summary": "Mock task classification",
            "feature_description": "Mock feature description",
            "justification": "Mock wave 1 classification",
        })
        return MockModelResponse(content=content)
    if "validator" in role_meta.lower() or "severity" in user_prompt.lower():
        content = json.dumps({"severity": "pass", "justification": "Mock validation pass"})
        return MockModelResponse(content=content)
    if "spec" in user_prompt.lower():
        return MockModelResponse(content="Refined spec: mock task specification")
    content = json.dumps([{"path": "src/mock.py", "content": "# Mock code", "action": "write"}])
    return MockModelResponse(content=content)


# Wrappers can preserve this marker without depending on function identity.
mock_completion_fn._is_mock = True  # type: ignore[attr-defined]


def is_mock_completion_fn(fn: Any) -> bool:
    """Return True if fn is mock_completion_fn or marked with _is_mock.

    Completion functions may be functools.partial instances; inspect their
    underlying function as well as the wrapper before consulting mock mode.
    """
    if fn is None:
        return False
    target = getattr(fn, "func", fn)
    if target == mock_completion_fn or getattr(target, "_is_mock", False) is True or getattr(fn, "_is_mock", False) is True:
        return True
    return is_mock_mode_active()


class MockAdapter(CoderAdapter):
    """Deterministic coder for testing without LLM calls.

    Default artifacts include a real test in the project's language so the
    ordinary quality gate can run. Explicit mock_files remain caller-owned;
    language selection must never silently replace a test's supplied output.
    """

    honoured_settings: frozenset[str] = frozenset({"mock_files"})

    def __init__(self, mock_files: Optional[list] = None, model: Optional[str] = None, **kwargs: Any):
        self.model = model or DEFAULT_MODEL
        self.mock_files = mock_files or [
            FileArtifact(path="src/hello.py", content="def hello():\n    return 'world'\n"),
            FileArtifact(path="tests/test_hello.py", content="from src.hello import hello\n\n\ndef test_hello():\n    assert hello() == 'world'\n"),
        ]
        self._mock_files_explicit = mock_files is not None
        self.call_count = 0
        self.last_spec: Optional[TaskSpec] = None
        self._completion_fn = mock_completion_fn

    def _bare_model(self) -> str:
        """The mock makes no LLM call, so there is no coder model to attribute."""
        return ""

    def implement(self, spec: TaskSpec) -> CodeArtifact:
        """Record the dispatch and return runnable, unprotected fixture files."""
        self.call_count += 1
        self.last_spec = spec
        language = spec.project_context.get("language", "").lower()
        if not self._mock_files_explicit and language in ("javascript", "typescript"):
            files = [
                FileArtifact(
                    path="src/hello.js",
                    content="function hello() { return 'world'; }\nmodule.exports = { hello };\n",
                ),
                FileArtifact(
                    path="src/hello.test.js",
                    content=(
                        "const { test } = require('node:test');\n"
                        "const assert = require('node:assert');\n"
                        "const { hello } = require('./hello.js');\n"
                        "\n"
                        "test('hello returns world', () => {\n"
                        "  assert.strictEqual(hello(), 'world');\n"
                        "});\n"
                    ),
                ),
            ]
        elif not self._mock_files_explicit and language == "go":
            files = [
                FileArtifact(path="go.mod", content="module mockfixture\n\ngo 1.20\n"),
                FileArtifact(path="src/hello.go", content="package hello\n\nfunc Hello() string { return \"world\" }\n"),
                FileArtifact(
                    path="src/hello_test.go",
                    content=(
                        "package hello\n\nimport \"testing\"\n\n"
                        "func TestHello(t *testing.T) {\n"
                        "\tif got := Hello(); got != \"world\" {\n"
                        "\t\tt.Fatalf(\"Hello() = %q, want %q\", got, \"world\")\n"
                        "\t}\n}\n"
                    ),
                ),
            ]
        elif not self._mock_files_explicit and language == "rust":
            files = [
                FileArtifact(
                    path="Cargo.toml",
                    content='[package]\nname = "mockfixture"\nversion = "0.1.0"\nedition = "2021"\n',
                ),
                FileArtifact(
                    path="src/lib.rs",
                    content=(
                        "pub fn hello() -> &'static str {\n    \"world\"\n}\n\n"
                        "#[cfg(test)]\nmod tests {\n    #[test]\n"
                        "    fn hello_returns_world() {\n        assert_eq!(super::hello(), \"world\");\n"
                        "    }\n}\n"
                    ),
                ),
            ]
        else:
            files = self.mock_files
        # Hermetic execution is not permission to mutate governance state.
        # Keep the same protected-path boundary as a live coder adapter.
        valid_files = [
            f for f in files
            if not is_protected_workspace_path(f.path, getattr(self, "workspace_dir", None) or Path.cwd())
        ]
        return CodeArtifact(files=valid_files)

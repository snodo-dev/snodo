"""Mock coder adapter for testing.

FILE: snodo/coders/mock.py (Fixes #12)

Returns deterministic outputs without making LLM calls.
"""

import json
from pathlib import Path
from typing import Any, Optional
from unittest.mock import MagicMock

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
    def __init__(self, content: str = "", tool_calls: Optional[list] = None):
        self.content = content
        self.tool_calls = tool_calls or []


class MockChoice:
    def __init__(self, message: MockChoiceMessage, finish_reason: str = "stop"):
        self.message = message
        self.finish_reason = finish_reason


class MockModelResponse:
    def __init__(self, content: str = "", tool_calls: Optional[list] = None, finish_reason: str = "stop"):
        self.choices = [MockChoice(MockChoiceMessage(content, tool_calls), finish_reason)]


def mock_completion_fn(*args: Any, **kwargs: Any) -> MockModelResponse:
    """Hermetic mock completion function. Never touches network or credentials."""
    messages = kwargs.get("messages", [])
    tools = kwargs.get("tools", [])

    # Check if tools contain submit_verdict or submit_files
    tool_names = set()
    if isinstance(tools, list):
        for t in tools:
            if isinstance(t, dict):
                fn = t.get("function", {})
                if isinstance(fn, dict) and "name" in fn:
                    tool_names.add(fn["name"])

    # LLMValidator tool loop
    if "submit_verdict" in tool_names:
        tc = MagicMock()
        tc.id = "mock_val_call_1"
        tc.function.name = "submit_verdict"
        tc.function.arguments = json.dumps({"severity": "pass", "justification": "Mock validation pass"})
        return MockModelResponse(content="", tool_calls=[tc])

    # LiteLLMAdapter tool loop
    if "submit_files" in tool_names:
        tc = MagicMock()
        tc.id = "mock_coder_call_1"
        tc.function.name = "submit_files"
        tc.function.arguments = json.dumps({"files": []})
        return MockModelResponse(content="", tool_calls=[tc])

    # Inspect messages to provide appropriate response
    user_prompt = ""
    for msg in reversed(messages):
        if isinstance(msg, dict) and msg.get("role") == "user":
            user_prompt = str(msg.get("content", ""))
            break

    # Wave classifier prompt
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

    # Validator prompt
    if "validator" in role_meta.lower() or "severity" in user_prompt.lower():
        content = json.dumps({"severity": "pass", "justification": "Mock validation pass"})
        return MockModelResponse(content=content)

    # Spec authoring prompt
    if "spec" in user_prompt.lower():
        content = "Refined spec: mock task specification"
        return MockModelResponse(content=content)

    # Default fallback content
    content = json.dumps([{"path": "src/mock.py", "content": "# Mock code", "action": "write"}])
    return MockModelResponse(content=content)


mock_completion_fn._is_mock = True  # type: ignore[attr-defined]


def is_mock_completion_fn(fn: Any) -> bool:
    """Return True if fn is mock_completion_fn or marked with _is_mock."""
    if fn is None:
        return False
    if fn == mock_completion_fn or getattr(fn, "_is_mock", False) is True:
        return True
    return is_mock_mode_active()


class MockAdapter(CoderAdapter):
    """Mock coder adapter for testing.

    Returns deterministic outputs without making LLM calls.
    Useful for fast, reliable unit tests.
    """

    def __init__(
        self,
        mock_files: Optional[list] = None,
        model: Optional[str] = None,
        **kwargs: Any,
    ):
        self.model = model or DEFAULT_MODEL
        self.mock_files = mock_files or [
            FileArtifact(path="src/hello.py", content="def hello():\n    return 'world'"),
            FileArtifact(path="tests/test_hello.py", content="def test_hello():\n    assert hello() == 'world'"),
        ]
        self.call_count = 0
        self.last_spec: Optional[TaskSpec] = None
        self._completion_fn = mock_completion_fn

    def implement(self, spec: TaskSpec) -> CodeArtifact:
        self.call_count += 1
        self.last_spec = spec

        valid_files = [
            f for f in self.mock_files
            if not (Path(f.path).parts and Path(f.path).parts[0] == ".snodo")
        ]
        return CodeArtifact(files=valid_files)

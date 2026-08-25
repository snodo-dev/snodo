"""Mock coder adapter for testing.

FILE: snodo/coders/mock.py

Returns deterministic outputs without making LLM calls.
"""

from pathlib import Path
from typing import Optional

from snodo.core.interfaces import TaskSpec, CodeArtifact, FileArtifact
from snodo.coders.base import CoderAdapter


class MockAdapter(CoderAdapter):
    """Mock coder adapter for testing.

    Returns deterministic outputs without making LLM calls.
    Useful for fast, reliable unit tests.
    """

    def __init__(
        self,
        mock_files: Optional[list] = None
    ):
        self.mock_files = mock_files or [
            FileArtifact(path="src/hello.py", content="def hello():\n    return 'world'"),
            FileArtifact(path="tests/test_hello.py", content="def test_hello():\n    assert hello() == 'world'"),
        ]
        self.call_count = 0
        self.last_spec: Optional[TaskSpec] = None

    def implement(self, spec: TaskSpec) -> CodeArtifact:
        self.call_count += 1
        self.last_spec = spec

        valid_files = [
            f for f in self.mock_files
            if not (Path(f.path).parts and Path(f.path).parts[0] == ".snodo")
        ]
        return CodeArtifact(files=valid_files)

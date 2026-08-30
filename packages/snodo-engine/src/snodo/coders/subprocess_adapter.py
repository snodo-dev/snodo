"""Base adapter for host CLI tools that write files in place via subprocess.

FILE: snodo/coders/subprocess_adapter.py

Abstracts host CLI tools (opencode-cli, agy) that execute in-place edits in the
working tree via subprocess invocation. Base class handles:
- Prompt formatting (_build_prompt)
- Subprocess invocation and error handling (missing binary, timeout, non-zero returncode)
- Git working tree readback (_read_changes_from_disk)
- Artifact construction (_diff_to_artifact)
- .snodo/ mutation protection and git commit (inherited from InPlaceCoderAdapter)
"""

import logging
import subprocess
from abc import abstractmethod
from pathlib import Path
from typing import Any, Optional

from snodo.coders.base import InPlaceCoderAdapter, LLMCallError
from snodo.core.interfaces import CodeArtifact, FileArtifact, TaskSpec

_logger = logging.getLogger(__name__)


class SubprocessCoderAdapter(InPlaceCoderAdapter):
    """Base coder adapter for host CLI subprocess tools."""

    skip_engine_commit: bool = True
    skip_workspace_write: bool = True

    binary: str = ""
    model_prefix: str = ""
    install_hint: str = ""
    timeout_seconds: int = 1800

    def __init__(
        self,
        model: str = "",
        temperature: float = 0.7,
        workspace: Optional[Path] = None,
        workspace_mcp: Optional[Any] = None,
        **kwargs: Any,
    ):
        self.model = model or self.model_prefix
        self.temperature = temperature

        if workspace is not None:
            self._workspace = workspace
        elif workspace_mcp is not None:
            from snodo.tools.workspace import WorkspaceMCP
            if isinstance(workspace_mcp, WorkspaceMCP):
                self._workspace = workspace_mcp.project_root
            else:
                self._workspace = Path.cwd()
        else:
            self._workspace = Path.cwd()

    def _bare_model(self) -> str:
        """Strip the registry prefix from the model string."""
        model = self.model
        if self.model_prefix and model.startswith(self.model_prefix):
            return model[len(self.model_prefix):]
        return model

    @abstractmethod
    def _build_argv(self, prompt: str, project_root: str, model: str) -> list[str]:
        """Construct the subprocess argument list for the specific CLI tool."""

    def _implement_in_place(self, spec: TaskSpec) -> CodeArtifact:
        prompt = self._build_prompt(spec)
        project_root = str(self._workspace)
        bare_model = self._bare_model()
        argv = self._build_argv(prompt, project_root, bare_model)

        try:
            proc = subprocess.run(
                argv,
                cwd=project_root,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
        except FileNotFoundError as e:
            raise LLMCallError(
                f"{self.binary} not found on PATH. {self.install_hint}"
            ) from e
        except subprocess.TimeoutExpired as e:
            raise LLMCallError(
                f"{self.binary} run timed out after {self.timeout_seconds}s"
            ) from e

        if proc.returncode != 0:
            tail = (proc.stderr or "")[:2000] or (proc.stdout or "")[:2000]
            raise LLMCallError(
                f"{self.binary} run failed (rc={proc.returncode}): {tail}"
            )

        diff_entries = self._read_changes_from_disk()
        if not diff_entries:
            combined = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
            tail = combined[-1000:]
            _logger.warning(
                "%s run completed but no changes detected (rc=0). output tail: %s",
                self.binary, tail,
            )

        return self._diff_to_artifact(diff_entries)

    def _diff_to_artifact(self, diff_entries: list) -> CodeArtifact:
        """Build a CodeArtifact from diff entries, re-reading content from disk."""
        _ignored_dirs = {"__pycache__", ".venv", "venv", "node_modules", ".git", ".pytest_cache", ".mypy_cache"}
        _ignored_exts = {".pyc", ".pyo", ".DS_Store"}

        files = []
        for entry in diff_entries:
            path = entry.get("file", "")
            if not path:
                continue
            parts = Path(path).parts
            if any(p in _ignored_dirs for p in parts) or Path(path).suffix in _ignored_exts:
                continue
            status = entry.get("status", "modified")

            if status == "deleted":
                files.append(FileArtifact(path=path, content="", action="delete"))
                continue

            file_path = Path(self._workspace) / path
            try:
                content = file_path.read_text()
            except Exception as exc:
                _logger.warning("%s: failed to read %s: %s", self.binary, file_path, exc)
                content = f"<unreadable: {exc}>"

            files.append(FileArtifact(path=path, content=content, action="write"))

        if not files:
            _logger.warning("%s returned no files — task completed with no changes", self.binary)
            return CodeArtifact(files=[])

        return CodeArtifact(files=files)

    def _build_prompt(self, spec: TaskSpec) -> str:
        """Build prompt from TaskSpec."""
        language = spec.project_context.get("language", "unknown")
        lang_hint = f" ({language} project)" if language != "unknown" else ""

        parts = [
            f"You are an expert software engineer{lang_hint}.",
            "Generate code based on the following specification.",
            "",
        ]

        structure = spec.project_context.get("structure", "")
        if structure:
            parts.append(f"## Directory Structure\n```\n{structure}\n```")
            parts.append("")

        if spec.memory_summary:
            parts.append(f"## Session History\n{spec.memory_summary}")
            parts.append("")

        parts.append(f"## Task\n{spec.description}")

        if spec.constraints:
            parts.append("\n## Constraints")
            for c in spec.constraints:
                parts.append(f"- {c}")

        parts.append("")
        parts.append(
            "Write the implementation to disk. Create all necessary files."
        )

        return "\n".join(parts)

"""OpenCode CLI coder adapter — shells `opencode run` on the host.

FILE: snodo/coders/opencode_cli_adapter.py

Runs opencode directly on the host machine (not in Docker) via::

    opencode run --dir <project_root> --dangerously-skip-permissions <prompt> -m <model>

Changes are read back from the working tree via git diff (opencode writes
files in-place).  Proven in experiments/arms/arm_a_opencode.py.
"""

import logging
import subprocess
from pathlib import Path
from typing import Any, Optional

from snodo.core.interfaces import TaskSpec, CodeArtifact, FileArtifact
from snodo.coders.base import CoderAdapter, LLMCallError

_logger = logging.getLogger(__name__)

_OPENCODE_TIMEOUT = 600  # 10 minutes


class OpenCodeCLIAdapter(CoderAdapter):
    """Coder adapter backed by the host ``opencode run`` CLI."""

    skip_engine_commit: bool = True
    skip_workspace_write: bool = True

    def __init__(
        self,
        model: str = "opencode/",
        temperature: float = 0.7,
        workspace: Optional[Path] = None,
        workspace_mcp: Optional[Any] = None,
        **kwargs,
    ):
        self.model = model
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
        """Strip the ``opencode-cli/`` prefix to get the bare model ID."""
        model = self.model
        if model.startswith("opencode-cli/"):
            return model[len("opencode-cli/"):]
        return model

    def implement(self, spec: TaskSpec) -> CodeArtifact:
        """Run ``opencode run`` on the host and read back changes via git.

        1. Build prompt from the TaskSpec
        2. Shell ``opencode run --dir <workspace> --dangerously-skip-permissions <prompt> -m <model>``
        3. Detect changed files via git diff (staged + unstaged + untracked)
        4. Build CodeArtifact from on-disk content
        """
        prompt = self._build_prompt(spec)
        project_root = str(self._workspace)

        try:
            proc = subprocess.run(
                [
                    "opencode", "run",
                    "--dir", project_root,
                    "--dangerously-skip-permissions",
                    prompt,
                    "-m", self._bare_model(),
                ],
                cwd=project_root,
                capture_output=True,
                text=True,
                timeout=_OPENCODE_TIMEOUT,
            )
        except FileNotFoundError:
            raise LLMCallError(
                "opencode not found on PATH. Install opencode: "
                "curl -fsSL https://opencode.ai/install | bash"
            )
        except subprocess.TimeoutExpired:
            raise LLMCallError(
                f"opencode run timed out after {_OPENCODE_TIMEOUT}s"
            )

        if proc.returncode != 0:
            tail = (proc.stderr or "")[:2000] or (proc.stdout or "")[:2000]
            raise LLMCallError(
                f"opencode run failed (rc={proc.returncode}): {tail}"
            )

        diff_entries = self._read_changes_from_disk()
        if not diff_entries:
            combined = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
            tail = combined[-1000:]
            _logger.warning(
                "opencode run completed but no changes detected "
                "(rc=0). output tail: %s", tail,
            )

        return self._diff_to_artifact(diff_entries)

    def _read_changes_from_disk(self) -> list:
        """Detect changed files via git in the workspace.

        Returns entries in the same ``{file, status}`` format as
        ``_diff_to_artifact`` expects.
        """
        from git import Repo, GitCommandError

        try:
            repo = Repo(str(self._workspace), search_parent_directories=True)
        except (GitCommandError, Exception) as exc:
            _logger.warning("git readback: cannot open repo at %s: %s", self._workspace, exc)
            return []

        changed: dict[str, str] = {}

        try:
            for d in repo.index.diff(None):
                path = d.b_path or d.a_path
                if path:
                    if d.change_type == "D":
                        changed[path] = "deleted"
                    else:
                        changed[path] = d.change_type

            for d in repo.index.diff("HEAD"):
                path = d.b_path or d.a_path
                if path and path not in changed:
                    changed[path] = d.change_type

            for path in repo.untracked_files:
                changed[path] = "added"
        except Exception as exc:
            _logger.warning("git readback: diff failed: %s", exc)
            return []

        entries = [{"file": path, "status": status} for path, status in changed.items()]
        _logger.debug("git readback: %d changed files", len(entries))
        return entries

    def _diff_to_artifact(self, diff_entries: list) -> CodeArtifact:
        """Build a CodeArtifact from diff entries, re-reading content from disk."""
        files = []
        for entry in diff_entries:
            path = entry.get("file", "")
            if not path:
                continue
            status = entry.get("status", "modified")

            if status == "deleted":
                files.append(FileArtifact(path=path, content="", action="delete"))
                continue

            file_path = self._workspace / path
            try:
                content = file_path.read_text()
            except Exception:
                content = ""

            files.append(FileArtifact(path=path, content=content, action="write"))

        if not files:
            _logger.warning("opencode-cli returned no files — task completed with no changes")
            return CodeArtifact(files=[])

        return CodeArtifact(files=files)

    def _build_prompt(self, spec: TaskSpec) -> str:
        """Build a prompt from the TaskSpec (mirrors OpenCodeAdapter)."""
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

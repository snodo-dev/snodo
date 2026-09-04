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
import os
import signal
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
        timeout_seconds: Optional[int] = None,
        **kwargs: Any,
    ):
        self.model = model or self.model_prefix
        self.temperature = temperature
        if timeout_seconds is not None:
            self.timeout_seconds = int(timeout_seconds)

        if workspace is not None:
            self._workspace = Path(workspace)
        elif workspace_mcp is not None:
            from snodo.tools.workspace import WorkspaceMCP
            if isinstance(workspace_mcp, WorkspaceMCP):
                self._workspace = Path(workspace_mcp.project_root)
            elif hasattr(workspace_mcp, "project_root") and workspace_mcp.project_root is not None:
                self._workspace = Path(workspace_mcp.project_root)
            else:
                raise ValueError(
                    f"{self.__class__.__name__} requires an explicit workspace or a valid "
                    f"WorkspaceMCP with project_root; received invalid workspace_mcp: {workspace_mcp!r}. "
                    "Inferring a containment boundary from Path.cwd() is prohibited (ADR 024/025)."
                )
        else:
            raise ValueError(
                f"{self.__class__.__name__} requires an explicit workspace or workspace_mcp; "
                "none was provided. Inferring a containment boundary from Path.cwd() is prohibited (ADR 024/025)."
            )

    def _bare_model(self) -> str:
        """Return the model to pass to the CLI, or "" to let it choose.

        An external coding agent owns its own model catalog — agy offers
        "Gemini 3.6 Flash (Medium)", opencode offers whatever its providers
        expose. snodo's ``-m`` names the model that JUDGES the work: it is
        resolved through litellm for the validators and the classifier, and it
        is meaningless to the CLI. Forwarding it produced:

            agy run failed (rc=1): invalid model selection
            (--model "deepseek/deepseek-v4-flash"): not recognized as a known
            model or custom model in settings

        So a model reaches the CLI only when the operator named one in THIS
        adapter's namespace (``agy/...``, ``opencode-cli/...``). Anything else
        yields "", and ``_build_argv`` omits the flag so the tool falls back to
        its own last-selected default.

        (Selecting the coder's model explicitly, while the validators keep
        theirs, needs a separate flag — ``-m`` cannot carry both.)
        """
        model = self.model
        if not self.model_prefix:
            return model
        prefixes = (self.model_prefix,)
        if self.model_prefix == "opencode-cli/":
            prefixes = ("opencode-cli/", "opencode/")
        for prefix in prefixes:
            if model.startswith(prefix):
                return model[len(prefix):]
        return ""

    @abstractmethod
    def _build_argv(self, prompt: str, project_root: str, model: str) -> list[str]:
        """Construct the subprocess argument list for the specific CLI tool."""

    def _run_subprocess(self, argv: list[str], project_root: str) -> subprocess.CompletedProcess:
        """Run CLI subprocess with process-group isolation and clean timeout termination."""
        proc = subprocess.Popen(  # noqa: S603
            argv,
            cwd=project_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            stdout, stderr = proc.communicate(timeout=self.timeout_seconds)
        except subprocess.TimeoutExpired as e:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, OSError):
                proc.kill()
            try:
                out, err = proc.communicate()
                stdout = (e.stdout or e.output or "") + (out or "")
                stderr = (e.stderr or "") + (err or "")
            except Exception:
                stdout = e.stdout or e.output or ""
                stderr = e.stderr or ""
            raise subprocess.TimeoutExpired(
                cmd=argv,
                timeout=self.timeout_seconds,
                output=stdout,
                stderr=stderr,
            ) from e

        return subprocess.CompletedProcess(
            args=argv,
            returncode=proc.returncode,
            stdout=stdout,
            stderr=stderr,
        )

    def _implement_in_place(self, spec: TaskSpec) -> CodeArtifact:
        prompt = self._build_prompt(spec)
        project_root = str(self._workspace)
        bare_model = self._bare_model()
        argv = self._build_argv(prompt, project_root, bare_model)

        _logger.info(
            "%s: executing with containment boundary at %s",
            self.binary, project_root,
        )

        timed_out = False
        timeout_tail = ""
        proc = None
        self.last_timed_out = False
        self.last_timeout_seconds = None
        self.last_timeout_tail = ""
        self.last_output_tail = ""

        try:
            proc = self._run_subprocess(argv, project_root)
        except FileNotFoundError as e:
            raise LLMCallError(
                f"{self.binary} not found on PATH. {self.install_hint}"
            ) from e
        except subprocess.TimeoutExpired as e:
            timed_out = True
            self.last_timed_out = True
            self.last_timeout_seconds = self.timeout_seconds
            out_str = e.stdout or e.output or ""
            err_str = e.stderr or ""
            if isinstance(out_str, bytes):
                out_str = out_str.decode("utf-8", errors="replace")
            if isinstance(err_str, bytes):
                err_str = err_str.decode("utf-8", errors="replace")
            out_tail = out_str.strip()[-2000:] if out_str else ""
            err_tail = err_str.strip()[-2000:] if err_str else ""
            tail = (out_tail or err_tail).strip()
            timeout_tail = tail
            self.last_timeout_tail = timeout_tail
            self.last_output_tail = timeout_tail

        if timed_out:
            msg = f"{self.binary} run timed out after {self.timeout_seconds}s"
            if timeout_tail:
                msg += f": {timeout_tail}"
            _logger.warning(msg)
            diff_entries = self._read_changes_from_disk()
            if not diff_entries:
                raise LLMCallError(msg)
            artifact = self._diff_to_artifact(diff_entries)
            if not artifact.files:
                raise LLMCallError(msg)
            if artifact and hasattr(artifact, "metadata") and isinstance(artifact.metadata, dict):
                artifact.metadata["timed_out"] = True
                artifact.metadata["timeout_seconds"] = self.timeout_seconds
                if timeout_tail:
                    artifact.metadata["output_tail"] = timeout_tail
            return artifact

        if proc.returncode != 0:
            out_str = proc.stdout or ""
            err_str = proc.stderr or ""
            if isinstance(out_str, bytes):
                out_str = out_str.decode("utf-8", errors="replace")
            if isinstance(err_str, bytes):
                err_str = err_str.decode("utf-8", errors="replace")
            out_tail = out_str.strip()[-2000:] if out_str else ""
            err_tail = err_str.strip()[-2000:] if err_str else ""
            tail = (out_tail or err_tail).strip()
            self.last_output_tail = tail
            msg = f"{self.binary} run failed (rc={proc.returncode})"
            if tail:
                msg += f": {tail}"
            _logger.warning(msg)
            diff_entries = self._read_changes_from_disk()
            if not diff_entries:
                raise LLMCallError(msg)
            artifact = self._diff_to_artifact(diff_entries)
            if not artifact.files:
                raise LLMCallError(msg)
            if artifact and hasattr(artifact, "metadata") and isinstance(artifact.metadata, dict):
                if tail:
                    artifact.metadata["output_tail"] = tail
            return artifact

        out_str = proc.stdout or ""
        err_str = proc.stderr or ""
        if isinstance(out_str, bytes):
            out_str = out_str.decode("utf-8", errors="replace")
        if isinstance(err_str, bytes):
            err_str = err_str.decode("utf-8", errors="replace")
        out_tail = out_str.strip()[-1000:] if out_str else ""
        err_tail = err_str.strip()[-1000:] if err_str else ""
        tail = (out_tail or err_tail).strip()
        self.last_output_tail = tail

        diff_entries = self._read_changes_from_disk()
        if not diff_entries:
            _logger.warning(
                "%s run completed but no changes detected (rc=0). output tail: %s",
                self.binary, tail,
            )

        return self._diff_to_artifact(diff_entries)

    def _diff_to_artifact(self, diff_entries: list) -> CodeArtifact:
        """Build a CodeArtifact from diff entries, re-reading content from disk."""
        _ignored_dirs = {"__pycache__", ".venv", "venv", "node_modules", ".git", ".pytest_cache", ".mypy_cache"}
        _ignored_exts = {".pyc", ".pyo", ".DS_Store"}

        meta: dict[str, Any] = {}
        if getattr(self, "last_output_tail", ""):
            meta["output_tail"] = self.last_output_tail

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
            return CodeArtifact(files=[], metadata=meta)

        return CodeArtifact(files=files, metadata=meta)

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

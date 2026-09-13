"""Base adapter for host CLI tools that write files in place via subprocess.

FILE: snodo/coders/subprocess_adapter.py

Abstracts host CLI tools (opencode-cli, agy) that execute in-place edits in the
working tree via subprocess invocation. Base class handles:
- Prompt formatting (_build_prompt)
- Subprocess invocation with live output streaming and error handling
  (missing binary, timeout, non-zero returncode)
- Git working tree readback (_read_changes_from_disk)
- Artifact construction (_diff_to_artifact)
- .snodo/ mutation protection and git commit (inherited from InPlaceCoderAdapter)
"""

import logging
import os
import signal
import subprocess
import threading
from abc import abstractmethod
from pathlib import Path
from typing import Any, Callable, Optional

from snodo.coders.base import InPlaceCoderAdapter, LLMCallError
from snodo.core.interfaces import CodeArtifact, FileArtifact, TaskSpec

_logger = logging.getLogger(__name__)

#: Trailing characters of EACH output stream that survive into the diagnostic
#: tail (``last_output_tail`` / ``last_timeout_tail`` / ``output_tail``).
#: One limit for all three exit paths (timeout, non-zero exit, zero-exit-with-
#: no-changes): they previously differed (2000 / 2000 / 1000) with no stated
#: reason, and the same fault must leave the same record no matter which path
#: produced it.
_OUTPUT_TAIL_CHARS = 2000


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
        """Run CLI subprocess with process-group isolation and live output streaming.

        Output is consumed while the process runs, not only after it exits. The
        old single blocking ``proc.communicate(timeout=...)`` hid a 58-minute
        coder narration behind one "Coder dispatched" line: everything the CLI
        wrote sat unread in a pipe until the run was over.

        The deadlock trap that made ``communicate`` look mandatory is real:
        a process writing heavily to stderr while the reader is blocked on
        stdout fills the OS pipe buffer and stalls the writer — and the reader
        with it. That is handled here by draining BOTH pipes concurrently, one
        reader thread per stream, so neither can ever fill up. Each line is
        appended to its stream's capture list (the full text is still returned
        for the diagnostic tail machinery) and, when the caller has supplied a
        ``progress_callback``, emitted to it at the moment it is read.

        The choice of whether a human sees the stream belongs to the caller,
        not the adapter: the engine wires ``progress_callback`` to its
        ``_progress`` sink, which prints to the foreground terminal or, for a
        background job, to the job's stdout.log that the dashboard and
        ``snodo job logs --watch`` tail. No callback means silent capture,
        exactly the pre-streaming behaviour.
        """
        proc = subprocess.Popen(  # noqa: S603
            argv,
            cwd=project_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            start_new_session=True,
        )
        emit = getattr(self, "progress_callback", None)
        out_chunks: list[str] = []
        err_chunks: list[str] = []

        def _reader(stream: Any, sink: list[str]) -> None:
            try:
                for line in iter(stream.readline, ""):
                    sink.append(line)
                    if emit is not None:
                        self._emit_coder_line(emit, line)
            except (ValueError, OSError):
                # Pipe closed underneath us (kill/EOF race); keep what was read.
                pass
            finally:
                try:
                    stream.close()
                except OSError:
                    pass

        readers = [
            threading.Thread(target=_reader, args=(proc.stdout, out_chunks), daemon=True),
            threading.Thread(target=_reader, args=(proc.stderr, err_chunks), daemon=True),
        ]
        for t in readers:
            t.start()

        try:
            proc.wait(timeout=self.timeout_seconds)
        except subprocess.TimeoutExpired as e:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, OSError):
                proc.kill()
            # The group is dead, so both pipes hit EOF; the bounded join keeps
            # the timeout path from hanging on a detached grandchild that the
            # kill never reached — partial output still beats no output.
            for t in readers:
                t.join(timeout=5)
            raise subprocess.TimeoutExpired(
                cmd=argv,
                timeout=self.timeout_seconds,
                output="".join(out_chunks),
                stderr="".join(err_chunks),
            ) from e

        for t in readers:
            t.join()

        return subprocess.CompletedProcess(
            args=argv,
            returncode=proc.returncode,
            stdout="".join(out_chunks),
            stderr="".join(err_chunks),
        )

    def _emit_coder_line(self, emit: Callable[[str], None], line: str) -> None:
        """Forward one raw output line to the caller's progress sink.

        The line is forwarded verbatim (minus its newline, which the sink's
        own printing supplies) — coder output is narration for a human, never
        a wire format, and nothing here inspects or rewrites it. A sink that
        fails must not kill the run it is watching, so errors are swallowed
        with a debug log rather than propagated.
        """
        try:
            emit(line.rstrip("\n"))
        except Exception:
            _logger.debug("%s: progress sink failed on coder output", self.binary, exc_info=True)

    @staticmethod
    def _combined_output_tail(stdout: str, stderr: str) -> str:
        """Build a diagnostic tail that keeps the end of BOTH output streams.

        A coder that narrates to stdout keeps that stream busy, so the *reason*
        a run stopped — budget exhaustion, a rate limit, a provider error —
        lands at the end of stderr. Choosing one stream (the old
        ``out_tail or err_tail``) discarded stderr entirely on exactly the runs
        that needed a diagnosis, and a single window over combined output
        allowed a busy stream to crowd the other one's ending out of the
        record. Tailing each stream under its own budget guarantees the end of
        the run survives in both channels: whatever the coder printed last, on
        either stream, is in the tail.

        When only one stream has content the tail is that stream's ending
        verbatim (no labels around a lone channel); when both have content
        they are returned as labeled sections.
        """
        out_tail = stdout.strip()[-_OUTPUT_TAIL_CHARS:] if stdout and stdout.strip() else ""
        err_tail = stderr.strip()[-_OUTPUT_TAIL_CHARS:] if stderr and stderr.strip() else ""
        if out_tail and err_tail:
            return f"[stdout]\n{out_tail}\n\n[stderr]\n{err_tail}"
        return out_tail or err_tail

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
            tail = self._combined_output_tail(out_str, err_str)
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
            tail = self._combined_output_tail(out_str, err_str)
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
        tail = self._combined_output_tail(out_str, err_str)
        self.last_output_tail = tail

        diff_entries = self._read_changes_from_disk()
        if not diff_entries:
            # Two different faults share this one shape: a coder that read the
            # code and DECIDED no change was needed, and a coder that STOPPED
            # before writing (an error on stderr under a busy stdout, a stream
            # that ends mid-tool-call). Telling them apart needs per-coder
            # output parsing, which ADR 034 keeps out of the adapter — so the
            # engine does not classify the difference, but the record must
            # let the operator see it: both stream endings are preserved
            # above, and the engine turns this into a no_file_operations halt
            # whose output_tail carries them.
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

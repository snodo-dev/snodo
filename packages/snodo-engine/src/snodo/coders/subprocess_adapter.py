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

import json
import logging
import os
import shutil
import signal
import subprocess
import threading
from abc import abstractmethod
from pathlib import Path
from typing import Any, Callable, Optional

from snodo.coders.base import (
    CoderTimeoutError,
    CoderUnavailableError,
    InPlaceCoderAdapter,
    LLMCallError,
)
from snodo.coders.report import (
    STOP_REASONS,
    CoderReport,
    parse_coder_report,
)
from snodo.core.interfaces import CodeArtifact, FileArtifact, TaskSpec

_logger = logging.getLogger(__name__)

#: Trailing characters of EACH output stream that survive into the diagnostic
#: tail (``last_output_tail`` / ``last_timeout_tail`` / ``output_tail``).
#: One limit for all three exit paths (timeout, non-zero exit, zero-exit-with-
#: no-changes): they previously differed (2000 / 2000 / 1000) with no stated
#: reason, and the same fault must leave the same record no matter which path
#: produced it.
_OUTPUT_TAIL_CHARS = 2000

#: Name of the file a subprocess coder is invited to write its report into,
#: relative to the JOB's own state directory (``.snodo/jobs/<job_id>/``). It is
#: never the user's project and never the worktree, and it is removed after the
#: run whether or not the coder wrote it (ADR 048; see :meth:`_report_path`).
_REPORT_FILENAME = "coder-report.json"

#: The stop-reason vocabulary, read from its single source so the invitation
#: cannot drift from the shape the engine parses (ADR 048).
_REPORT_STOP_REASONS = "|".join(sorted(STOP_REASONS))

#: Instruction appended to a subprocess coder's prompt when the engine can offer
#: somewhere to write. It asks ONLY for the report — it must never change what
#: the coder is asked to build — and it says the report is optional, because a
#: coder that does the work and forgets is the ordinary case (ADR 048).
_REPORT_INSTRUCTION = (
    "\n\n## Report (optional)\n"
    "Before you exit, write a JSON object describing this run to:\n"
    "  {report_path}\n"
    "Use this shape and omit any field you do not know (do not invent "
    "fields):\n"
    '{{"files": [{{"path": "<workspace-relative>", "kind": '
    '"created|modified|deleted"}}], "turns_used": <int>, '
    '"turns_available": <int>, "tokens_used": <int>, '
    '"context_window": <int>, "wall_time_ms": <int>, "stop_reason": '
    '"{stop_reasons}", "findings": "<findings text if non-diff task>"}}\n'
    "The engine reads this file and deletes it afterwards. It is not part of "
    "the task; leaving it unwritten changes nothing about your work."
)


class SubprocessCoderAdapter(InPlaceCoderAdapter):
    """Base coder adapter for host CLI subprocess tools."""

    skip_engine_commit: bool = True
    skip_workspace_write: bool = True

    binary: str = ""
    model_prefix: str = ""
    install_hint: str = ""
    timeout_seconds: int = 1800

    #: Arguments that make the host CLI print its own version. Overridable per
    #: adapter because there is no cross-tool convention; a tool that does not
    #: answer leaves the version empty rather than failing the run.
    version_args: tuple = ("--version",)

    #: Resolved path and self-reported version of the binary THIS run invoked.
    #: Empty until :meth:`_implement_in_place` resolves them. Read by the
    #: engine so a halt payload can say which binary produced the run — two
    #: installations of the same tool are otherwise indistinguishable after the
    #: fact (Fixes #290).
    last_binary_path: str = ""
    last_binary_version: str = ""

    #: This run's best-effort account of what it did and why it stopped (ADR
    #: 048), read back from the file the coder was invited to write. ``None``
    #: when the coder wrote nothing, wrote a malformed report, or was never
    #: offered a path — the ordinary case, and never an error. Nothing reads it
    #: yet; it is recorded and that is all (Fixes #318).
    last_report: Optional[CoderReport] = None

    #: What a subprocess coder can actually honour: the model reaches the argv
    #: (prefix-gated, see :meth:`_bare_model`) and ``timeout_seconds`` bounds
    #: the wait on the child. It does NOT honour ``max_tokens`` or
    #: ``max_tool_turns`` — the spawned program runs its own loop and the
    #: values never reach it — and ``temperature``, though the constructor
    #: stores it, is never read again, so it is declared absent: stored-and-
    #: never-read is inert exactly like never-arrived (Fixes #311).
    honoured_settings: frozenset[str] = frozenset(
        {"model", "timeout_seconds", "workspace"}
    )

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

    @classmethod
    def availability_requirements(cls) -> tuple:
        """A host-CLI coder needs its binary on the invoker's PATH.

        Declared from ``binary``/``install_hint`` so every subprocess adapter
        carries its own requirement without repeating it: a dispatcher checks
        availability in the process that will run the coder, before any task
        is dispatched (readiness runs in the operator's shell, which may have
        a different PATH).
        """
        if not cls.binary:
            return ()
        return ((cls.binary, cls.install_hint),)

    def _resolve_binary_path(self) -> str:
        """Return the absolute path of the binary PATH resolution picks HERE.

        DECISION — should a long-running server re-resolve the coder rather
        than trust the environment it was born with? No, and it does not have
        to: ``subprocess`` resolves the bare name in the argv from the CHILD's
        environment, which is the server's environment inherited at spawn.
        Re-resolving to the operator's live shell PATH would record a binary
        the run never used — a lie in the audit trail. PATH resolution at
        invocation is already correct; what was missing was recording its
        result. So the adapter resolves in the process that will actually run
        the coder, at the moment it runs, and keeps the answer (Fixes #290).
        """
        return shutil.which(self.binary) or ""

    def _read_binary_version(self, binary_path: str) -> str:
        """Return the version *binary_path* reports, via the shared reader.

        The rule for what counts as a tool's version lives in
        ``snodo.coders.availability`` so the readiness checker and the adapter
        record agree (Fixes #290).
        """
        from snodo.coders.availability import read_binary_version

        return read_binary_version(binary_path, self.version_args)

    def _record_binary_provenance(self) -> None:
        """Resolve and remember the binary path and version for THIS run.

        Called once per dispatch, before the CLI is spawned, so the record is
        per-run and overwritten on the next (the same rule the halt payload's
        other per-run facts follow).
        """
        self.last_binary_path = self._resolve_binary_path()
        self.last_binary_version = self._read_binary_version(self.last_binary_path)

    def _coder_report_dir(self) -> Optional[Path]:
        """Return the snodo-state directory a coder may write its report in.

        The report lives in the JOB's own state (``.snodo/jobs/<job_id>/``),
        never in the user's project or the granted workspace. The project root
        is read from ``SNODO_PROJECT_ROOT`` — the same authoritative source
        ``UsageTracker`` uses — and never guessed by walking the filesystem
        (ADR 024/025). A task-state directory is the fallback for an inline run
        with no job. The directory must already exist: the engine does not
        create state to hold an optional report, so a run with no job state
        simply never gets an invitation (the ordinary case, ADR 048).
        """
        project_root = os.environ.get("SNODO_PROJECT_ROOT", "")
        if not project_root:
            return None
        try:
            root = Path(project_root)
            job_id = getattr(self, "_job_id", "") or os.environ.get("SNODO_JOB_ID", "")
            if job_id.startswith("j_"):
                job_dir = root / ".snodo" / "jobs" / job_id
                if job_dir.is_dir():
                    return job_dir
            task_id = getattr(self, "_task_id", "") or ""
            if task_id.startswith("task_"):
                task_dir = root / ".snodo" / "tasks" / task_id
                if task_dir.is_dir():
                    return task_dir
        except (OSError, ValueError):
            return None
        return None

    def _report_path(self) -> Optional[Path]:
        """The agreed path a coder is invited to write its report to."""
        report_dir = self._coder_report_dir()
        return (report_dir / _REPORT_FILENAME) if report_dir else None

    def _invite_report(self, prompt: str) -> str:
        """Append the report invitation to *prompt*, or leave it untouched.

        The invitation asks only for the report and says it is optional: the
        presence or absence of the instruction must not change what the coder
        is asked to build (ADR 048). When no job state can host the file, the
        prompt is returned verbatim — exactly today's prompt.
        """
        report_path = self._report_path()
        if report_path is None:
            return prompt
        return prompt + _REPORT_INSTRUCTION.format(
            report_path=str(report_path),
            stop_reasons=_REPORT_STOP_REASONS,
        )

    def _read_coder_report(self, report_path: Optional[Path]) -> Optional[CoderReport]:
        """Read and parse the report a coder may have written, or ``None``.

        A missing file is the ordinary case and silent; a file that is not
        valid JSON, or that does not fit the shape, is discarded with one log
        line by :func:`parse_coder_report` and treated exactly like an absent
        report. Never raises — a coder's misstatement must not fail a run the
        worktree can be judged on its own for (ADR 048).
        """
        if report_path is None:
            return None
        try:
            if not report_path.is_file():
                return None
            raw = json.loads(report_path.read_text())
        except (OSError, ValueError) as exc:
            _logger.warning(
                "%s: discarding unreadable coder report at %s: %s: %s",
                self.binary, report_path, type(exc).__name__, exc,
            )
            return None
        return parse_coder_report(raw)

    def _discard_coder_report(self, report_path: Optional[Path]) -> None:
        """Remove the report file so nothing is left behind, best-effort.

        The report is engine scratch, not the coder's deliverable: it must not
        survive the run to appear in a diff, a commit or a later worktree read.
        A failure to remove it is recorded and swallowed — it is not the run's
        fault and not the run's outcome (ADR 048).
        """
        if report_path is None:
            return
        try:
            report_path.unlink(missing_ok=True)
        except OSError as exc:
            _logger.warning(
                "%s: could not remove coder report at %s: %s",
                self.binary, report_path, exc,
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
        for prefix in self.model_prefixes():
            if model.startswith(prefix):
                return model[len(prefix):]
        return ""

    @classmethod
    def model_prefixes(cls) -> tuple[str, ...]:
        """Return namespaces this adapter accepts in configured model names."""
        prefixes = (cls.model_prefix,)
        if cls.model_prefix == "opencode-cli/":
            prefixes = ("opencode-cli/", "opencode/")
        return prefixes

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
            stdin=subprocess.DEVNULL,
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

        Giving the coder's live stream the validator's turn shape (elapsed,
        turn count, what the turn did) is the sink's presentation job, not
        this one: it happens downstream in ``ProgressRenderer``, on the
        interactive path only, so the capture, a job's stdout.log and every
        plain stream keep these bytes untouched (Issue #306).
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
        """Run the coder, then read back any report it was invited to write.

        The report is per-run scratch: it is reset here, the coder is offered
        somewhere in the job's own state to write it, and whatever it left
        behind is read and deleted no matter how the run ends — success, a
        non-zero exit, or a timeout (ADR 048). Reading it changes nothing about
        the artifact or the outcome; a missing report is the ordinary case and
        is silent (Fixes #318).
        """
        report_path = self._report_path()
        self.last_report = None
        try:
            return self._run_in_place(spec)
        finally:
            self.last_report = self._read_coder_report(report_path)
            self._discard_coder_report(report_path)

    def _run_in_place(self, spec: TaskSpec) -> CodeArtifact:
        prompt = self._invite_report(self._build_prompt(spec))
        project_root = str(self._workspace)
        bare_model = self._bare_model()
        argv = self._build_argv(prompt, project_root, bare_model)

        # Resolve and record which binary this run is about to invoke, in THIS
        # process's environment, before spawning it. The argv carries the bare
        # name; the child's PATH resolves it, and that resolution is normally
        # correct — what was missing was any record of its result (Fixes #290).
        self._record_binary_provenance()
        _logger.info(
            "%s: executing with containment boundary at %s (binary=%s version=%s)",
            self.binary, project_root,
            self.last_binary_path or "unresolved",
            self.last_binary_version or "unknown",
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
            # The program is not installed where the run executes. That is an
            # environment fault, not a coder-configuration fault and never a
            # verdict about the task: CoderUnavailableError says so in its
            # type, and carries the install command in its message. Name the
            # PATH that was searched: the operator can see the binary on THEIR
            # PATH, so "not on PATH" without the path searched sends them
            # looking in the wrong place (Fixes #290).
            raise CoderUnavailableError(
                self.binary, self.install_hint, search_path=os.environ.get("PATH", ""),
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
                # Nothing the adapter can see in the worktree. The run may
                # still have committed its work earlier in this run, or a
                # previous attempt's commit may already be on the branch: the
                # engine consults the branch before declaring a fault. Raise the
                # operational timeout — not a generic coder failure — so the
                # engine can look and classify honestly (Fixes #281).
                raise CoderTimeoutError(msg, timeout_seconds=self.timeout_seconds)
            artifact = self._diff_to_artifact(diff_entries)
            if not artifact.files:
                raise CoderTimeoutError(msg, timeout_seconds=self.timeout_seconds)
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

        findings = getattr(self.last_report, "findings", None) if self.last_report else None
        if findings is not None:
            meta["findings"] = findings

        if not files:
            _logger.warning("%s returned no files — task completed with no changes", self.binary)
            return CodeArtifact(files=[], metadata=meta, findings=findings)

        return CodeArtifact(files=files, metadata=meta, findings=findings)

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

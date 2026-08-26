"""Quality Validator - language-agnostic post-execute validation.

FILE: snodo/validators/quality.py (Task 6.2)

Runs the repository's own test suite after execution.
Reads test_command from protocol validator tooling config.
Auto-detects language if not specified.

Input context: working_directory + branch (full repo state).
NOT artifact_paths or generated code snippets.
"""

import subprocess
from pathlib import Path
from typing import Any, Optional

from snodo.compiler.models import Validator
from snodo.core.interfaces import ValidatorResult
from snodo.validators.context import ValidatorBase
from snodo.validators.registry import _default_registry

# Auto-detection rules: (marker file, test command)
_DETECT_RULES = [
    ("package.json", "npm test"),
    ("pyproject.toml", "pytest"),
    ("setup.py", "pytest"),
    ("setup.cfg", "pytest"),
    ("Cargo.toml", "cargo test"),
    ("Makefile", "make test"),
    ("go.mod", "go test ./..."),
]

# Bound on the stdout/stderr tail surfaced in a failure message.
_OUTPUT_TAIL_CHARS = 400


def _decode(data) -> str:
    """Decode subprocess output that may be bytes (TimeoutExpired stdout/stderr)."""
    if data is None:
        return ""
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return str(data)


class QualityValidator(ValidatorBase):
    """Post-execute validator that runs the repo's test suite."""

    DEFAULT_TIMEOUT = 300

    def __init__(
        self,
        validator_spec: Validator,
        working_directory: str = "",
    ):
        self.validator_spec = validator_spec
        self.working_directory = Path(working_directory).resolve() if working_directory else Path.cwd()
        self.validator_id = validator_spec.validator_id

    @classmethod
    def registered_type(cls) -> str:
        return "quality"

    def evaluate(self, context=None) -> ValidatorResult:
        """Run the test suite and return a ValidatorResult.

        Returns:
            ValidatorResult:
                - "pass" if tests pass (exit code 0)
                - "blocker" (error=False) if tests genuinely fail (non-zero exit)
                - "blocker" with error=True if the fault is operational (no test
                  command resolvable, command not found, not executable, timeout)
                  — surfaced as validator_error, not a judgement
        """
        # Backward-compat: old code calls evaluate() with no args
        if context is not None and context.working_directory:
            self.working_directory = Path(context.working_directory).resolve()

        test_command = self._resolve_test_command()

        if test_command is None:
            return ValidatorResult(
                validator_id=self.validator_id,
                severity="blocker",
                error=True,
                justification=(
                    "No test command resolvable: no tooling.test_command is "
                    "configured and no language marker file (package.json, "
                    "pyproject.toml, setup.py, setup.cfg, Cargo.toml, Makefile, "
                    "go.mod) was detected. Set tooling.test_command in the "
                    "protocol's quality validator config."
                ),
            )

        timeout = self._get_timeout()

        return self._run_command(test_command, timeout, context=context)

    def _resolve_test_command(self) -> Optional[str]:
        """Resolve the test command from tooling config or auto-detection.

        Returns:
            Test command string, or None if cannot determine.
        """
        # 1. Check tooling config
        tooling = self.validator_spec.tooling
        if tooling and tooling.get("test_command"):
            return tooling["test_command"]

        # 2. Auto-detect from project files
        return self._auto_detect()

    def _auto_detect(self) -> Optional[str]:
        """Auto-detect test command from project marker files.

        Returns:
            Test command string, or None if no markers found.
        """
        for marker_file, command in _DETECT_RULES:
            if (self.working_directory / marker_file).exists():
                return command
        return None

    def _get_timeout(self) -> float:
        """Get timeout from tooling config or default."""
        tooling = self.validator_spec.tooling
        if tooling and tooling.get("timeout"):
            return float(tooling["timeout"])
        return self.DEFAULT_TIMEOUT

    def _resolve_commit_hash(self) -> str:
        """Resolve current git commit hash for working_directory."""
        try:
            res = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=str(self.working_directory),
                capture_output=True,
                text=True,
                timeout=5,
            )
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout.strip()
        except Exception:
            pass
        return "uncommitted"

    def _audit_verification(
        self,
        context: Optional[Any],
        command: str,
        commit_hash: str,
        returncode: int,
        outcome: str,
        output_tail: str = "",
    ) -> None:
        """Appends a first-class verification_executed event to audit_log."""
        audit_log = getattr(context, "audit_log", None) if context else None
        if audit_log is None:
            try:
                from snodo.infrastructure.audit import get_audit_log
                audit_log = get_audit_log()
            except Exception:
                pass

        if audit_log and hasattr(audit_log, "append_event"):
            task_ref = ""
            session_id = ""
            if context:
                task_ref = getattr(getattr(context, "task", None), "id", "") or getattr(context, "task_id", "") or ""
                session_id = getattr(context, "job_id", "") or ""

            try:
                audit_log.append_event("verification_executed", {
                    "op": "verification_executed",
                    "command": command,
                    "commit": commit_hash,
                    "returncode": returncode,
                    "outcome": outcome,
                    "validator_id": self.validator_id,
                    "task_ref": task_ref,
                    "session_id": session_id,
                    "working_directory": str(self.working_directory),
                    "output_tail": output_tail[:400] if output_tail else "",
                })
            except Exception:
                pass

    def _run_command(self, command: str, timeout: float, context: Optional[Any] = None) -> ValidatorResult:
        """Run a test command and return the result.

        Args:
            command: Shell command string to execute
            timeout: Maximum execution time in seconds
            context: Optional ValidatorContext for audit logging

        Returns:
            ValidatorResult based on exit code and evidence:
            - exit 0 → pass
            - exit 126 / 127 (or FileNotFoundError / PermissionError) → an
              operational fault (error=True), not a judgement about the work
            - any other non-zero exit → blocker (a genuine test result)
            - timeout → an operational fault (error=True)
        """
        if timeout is None or timeout <= 0:
            timeout = self.DEFAULT_TIMEOUT

        commit_hash = self._resolve_commit_hash()

        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=str(self.working_directory),
                capture_output=True,
                text=True,
                timeout=timeout,
            )

            if result.returncode == 0:
                summary = self._extract_summary(result.stdout)
                self._audit_verification(
                    context, command, commit_hash, 0, "pass", summary
                )
                return ValidatorResult(
                    validator_id=self.validator_id,
                    severity="pass",
                    justification=f"Tests passed: {summary}",
                )

            res = self._classify_failure(command, result.returncode,
                                          result.stdout, result.stderr)
            outcome = "error" if getattr(res, "error", False) else "fail"
            tail = self._output_tail(result.stdout, result.stderr)
            self._audit_verification(
                context, command, commit_hash, result.returncode, outcome, tail
            )
            return res

        except FileNotFoundError:
            self._audit_verification(
                context, command, commit_hash, 127, "error", "Test command not found"
            )
            return ValidatorResult(
                validator_id=self.validator_id,
                severity="blocker",
                error=True,
                justification=(
                    f"Test command not found: '{command}'. Install the test "
                    "runner or set tooling.test_command in the protocol's "
                    "quality validator config."
                ),
            )
        except PermissionError:
            self._audit_verification(
                context, command, commit_hash, 126, "error", "Test command not executable"
            )
            return ValidatorResult(
                validator_id=self.validator_id,
                severity="blocker",
                error=True,
                justification=(
                    f"Test command is not executable: '{command}'. Check file "
                    "permissions or set tooling.test_command in the protocol's "
                    "quality validator config."
                ),
            )
        except subprocess.TimeoutExpired as e:
            tail = self._output_tail(_decode(e.stdout), _decode(e.stderr))
            self._audit_verification(
                context, command, commit_hash, -1, "error", f"Timeout after {timeout}s"
            )
            return ValidatorResult(
                validator_id=self.validator_id,
                severity="blocker",
                error=True,
                justification=(
                    f"Test command timed out after {timeout}s: '{command}'. "
                    "Increase tooling.timeout or investigate why the suite "
                    "is stalling."
                ),
            )

    def _classify_failure(self, command: str, returncode: int,
                          stdout: str, stderr: str) -> ValidatorResult:
        """Classify a non-zero exit as operational vs judgement, by evidence.

        Exit 126 and 127 are reserved by the shell for "command found but not
        executable" and "command not found" respectively, and are corroborated
        against the command's stderr before being treated as operational faults.
        Any other non-zero exit is a genuine test result (a judgement).
        """
        output = f"{stdout or ''}\n{stderr or ''}"

        if returncode == 127 and self._evidence_command_not_found(output):
            tail = self._output_tail(stdout, stderr)
            return ValidatorResult(
                validator_id=self.validator_id,
                severity="blocker",
                error=True,
                justification=(
                    f"Test command not found: '{command}'. Install the test "
                    "runner or set tooling.test_command in the protocol's "
                    f"quality validator config. Output: {tail}"
                ),
            )

        if returncode == 126 and self._evidence_not_executable(output):
            tail = self._output_tail(stdout, stderr)
            return ValidatorResult(
                validator_id=self.validator_id,
                severity="blocker",
                error=True,
                justification=(
                    f"Test command is not executable: '{command}'. Check file "
                    "permissions or set tooling.test_command in the protocol's "
                    f"quality validator config. Output: {tail}"
                ),
            )

        # A genuine test failure must carry the evidence the coder needs to fix
        # it: the bounded stdout/stderr tail, not a one-line summary.  The
        # summary collapses the failing test name, assertion and file into
        # "2 failed", which a recovery spec then relays as an invisible failure
        # the coder can only guess at (see ADR 021).
        tail = self._output_tail(stdout, stderr)
        return ValidatorResult(
            validator_id=self.validator_id,
            severity="blocker",
            justification=f"Tests failed (exit {returncode}). Output:\n{tail}",
        )

    @staticmethod
    def _evidence_command_not_found(output: str) -> bool:
        lowered = output.lower()
        return "not found" in lowered or "command not found" in lowered

    @staticmethod
    def _evidence_not_executable(output: str) -> bool:
        lowered = output.lower()
        return "permission denied" in lowered or "is a directory" in lowered

    def _output_tail(self, stdout: str, stderr: str) -> str:
        """Return a bounded tail of the command's combined output."""
        combined = f"{stdout or ''}\n{stderr or ''}".strip()
        if not combined:
            return "(no output)"
        if len(combined) <= _OUTPUT_TAIL_CHARS:
            return combined
        return "...\n" + combined[-_OUTPUT_TAIL_CHARS:]

    def _extract_summary(self, output: str) -> str:
        """Extract the last meaningful line from command output.

        Args:
            output: Raw command output

        Returns:
            Summary string (max 200 chars)
        """
        if not output or not output.strip():
            return "no output"

        for line in reversed(output.strip().split("\n")):
            line = line.strip()
            if line:
                return line[:200]
        return "no output"

_default_registry.register(QualityValidator.registered_type(), QualityValidator)

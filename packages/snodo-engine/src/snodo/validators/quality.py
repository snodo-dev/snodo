"""Quality Validator - language-agnostic post-execute validation.

FILE: snodo/validators/quality.py (Task 6.2)

Runs the repository's own test suite after execution.
Reads test_command from protocol validator tooling config.
Auto-detects language if not specified.

Input context: working_directory + branch (full repo state).
NOT artifact_paths or generated code snippets.
"""

import logging
import subprocess
from pathlib import Path
from typing import Any, Optional

from snodo.compiler.models import Validator
from snodo.core.interfaces import ValidatorResult
from snodo.validators.context import ValidatorBase
from snodo.validators.registry import _default_registry

logger = logging.getLogger(__name__)

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

# The no-op default test command shipped by every protocol template that has a
# quality validator. It is a POSIX shell command that prints a notice and exits
# zero, so a project with no tooling.test_command and no detectable marker file
# can always run its first task instead of halting on "No test command
# resolvable" (the literal `REPLACE_ME` some templates shipped reached the
# shell and exited 127).
#
# Auto-detection and `snodo init --test-command` take precedence over it: it is
# treated as "not configured", so a marker file in the tree is still picked up,
# and it is used only when neither produces anything. When the validator does
# run it, the audit record states outcome ``NO_TESTS_OUTCOME`` ("no_tests") and
# that NO tests were executed — never a pass for work that did not run (see
# ADR 031).
NOOP_TEST_COMMAND = "echo 'snodo: no test_command configured; no tests executed'"

# Values of tooling.test_command that mean "no test command has been configured
# yet" and must therefore defer to auto-detection and, failing that, to the
# no-op default above. `REPLACE_ME` is honoured for projects initialised from
# pre-fix greenfield templates; NOOP_TEST_COMMAND is the shipped default.
_PLACEHOLDER_COMMANDS = frozenset({"", "REPLACE_ME", NOOP_TEST_COMMAND})

# Audit `outcome` recorded when the configured no-op ran. Distinct from "pass"
# so an operator reading the audit trail, and the cloud counting ungated
# projects, can tell a real pass from a placeholder.
NO_TESTS_OUTCOME = "no_tests"


def _is_no_op(command: Optional[str]) -> bool:
    """Return True when *command* is the configured no-op default."""
    return (command or "").strip() == NOOP_TEST_COMMAND


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
                - "pass" if tests pass (exit code 0). When the configured no-op
                  default ran (no test framework detected), the result is "pass"
                  but the audit record states "no tests executed" rather than
                  claiming "Tests passed".
                - "blocker" (error=False) if tests genuinely fail (non-zero exit)
                - "blocker" with error=True if the fault is operational (command
                  not found, not executable, timeout) — surfaced as
                  validator_error, not a judgement
        """
        # Backward-compat: old code calls evaluate() with no args
        if context is not None and context.working_directory:
            self.working_directory = Path(context.working_directory).resolve()

        test_command = self._resolve_test_command()

        timeout = self._get_timeout()

        return self._run_command(test_command, timeout, context=context)

    def _resolve_test_command(self) -> str:
        """Resolve the test command from tooling config or auto-detection.

        Precedence:
            1. ``tooling.test_command`` — unless it is a placeholder (the
               shipped no-op default or the legacy ``REPLACE_ME``), which means
               "no test command configured yet";
            2. auto-detection from repository marker files;
            3. the no-op default (``NOOP_TEST_COMMAND``) — a POSIX shell
               command that prints a notice and exits zero, so a project can
               always run.

        Returns:
            A test command string. Never None.
        """
        # 1. Check tooling config. The no-op sentinel is not a real command:
        #    it is the shipped default, and auto-detection still takes
        #    precedence over it.
        tooling = self.validator_spec.tooling
        configured = tooling.get("test_command") if tooling else None
        if configured and configured not in _PLACEHOLDER_COMMANDS:
            return configured

        # 2. Auto-detect from project files.
        detected = self._auto_detect()
        if detected:
            return detected

        # 3. No-op default — a command that exists on a POSIX shell and exits 0.
        return NOOP_TEST_COMMAND

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
                ["git", "rev-parse", "HEAD"],  # noqa: S607 - git resolved from PATH by design; argv list, no shell, fully controlled flags
                cwd=str(self.working_directory),
                capture_output=True,
                text=True,
                timeout=5,
            )
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout.strip()
        except Exception as e:
            logger.debug("Failed to resolve git commit hash: %s", e)
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
        """Append a first-class verification_executed event to audit_log.

        The audit log is taken ONLY from the validator context.  It is never
        resolved from the current working directory (Fixes #65): the code
        under test is not always the project under test, and a cwd-relative
        ``get_audit_log()`` (default ``.snodo/audit.log``) can point at an
        unrelated repository — under ``pytest -n auto`` concurrent workers
        then append to that file simultaneously, corrupt the hash chain, and
        break the next run.  A verification event without an explicit audit
        log in context is skipped, not invented from cwd.
        """
        audit_log = getattr(context, "audit_log", None) if context else None

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
            except Exception as e:
                logger.warning("Failed to log verification event to audit log: %s", e)

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
            result = subprocess.run(  # noqa: S602 - command is an operator-authored shell string (tooling.test_command or auto-detect literal, e.g. "npm test" / "py.test && flake8"); compound commands require a shell. Source is the trusted protocol file (ADR 014 / #110), executed as the repo owner.
                command,
                shell=True,
                cwd=str(self.working_directory),
                capture_output=True,
                text=True,
                timeout=timeout,
            )

            if result.returncode == 0:
                if _is_no_op(command):
                    # The configured no-op default ran. The task proceeds
                    # (severity "pass") but the record must not claim tests
                    # passed: no tests were executed. The `skipped` marker lets
                    # the engine surface the ungated run in normal output (a
                    # plain pass is only shown in verbose mode).
                    summary = self._extract_summary(result.stdout)
                    self._audit_verification(
                        context, command, commit_hash, 0, NO_TESTS_OUTCOME, summary
                    )
                    return ValidatorResult(
                        validator_id=self.validator_id,
                        severity="pass",
                        justification=(
                            "No tests executed: no test command is configured "
                            "and no test framework was detected, so the "
                            "no-op default ran. Set tooling.test_command in "
                            "the protocol's quality validator config to run "
                            "a real suite."
                        ),
                        skipped=True,
                    )
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

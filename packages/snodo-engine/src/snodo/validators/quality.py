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
from typing import Optional

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
        # Backward-compat: old code calls evaluate() with no args
        if context is not None and context.working_directory:
            self.working_directory = Path(context.working_directory).resolve()
        """Run the test suite and return a ValidatorResult.

        Returns:
            ValidatorResult:
                - "pass" if tests pass (exit code 0)
                - "blocker" if tests fail (exit code != 0)
                - "warn" if command not found or cannot determine test command
        """
        test_command = self._resolve_test_command()

        if test_command is None:
            return ValidatorResult(
                validator_id=self.validator_id,
                severity="warn",
                justification="Cannot determine test command. "
                "Set tooling.test_command in protocol validator config.",
            )

        timeout = self._get_timeout()

        return self._run_command(test_command, timeout)

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

    def _run_command(self, command: str, timeout: float) -> ValidatorResult:
        """Run a test command and return the result.

        Args:
            command: Shell command string to execute
            timeout: Maximum execution time in seconds

        Returns:
            ValidatorResult based on exit code
        """
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
                return ValidatorResult(
                    validator_id=self.validator_id,
                    severity="pass",
                    justification=f"Tests passed: {summary}",
                )
            else:
                summary = self._extract_summary(result.stdout or result.stderr)
                return ValidatorResult(
                    validator_id=self.validator_id,
                    severity="blocker",
                    justification=f"Tests failed (exit {result.returncode}): {summary}",
                )

        except FileNotFoundError:
            return ValidatorResult(
                validator_id=self.validator_id,
                severity="warn",
                justification=f"Command not found: {command}",
            )
        except subprocess.TimeoutExpired:
            return ValidatorResult(
                validator_id=self.validator_id,
                severity="blocker",
                justification=f"Tests timed out after {timeout}s",
            )

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

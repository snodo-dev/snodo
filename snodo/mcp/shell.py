"""Shell/Test Runner MCP server for automated validation.

FILE: snodo/mcp/shell.py

Implements automated validation via test execution with strict command whitelisting.
Converts test results to ValidatorResult format for protocol integration.
"""

import subprocess
import re
import sys
from pathlib import Path
from typing import List, Optional

# Import ValidatorResult from core interfaces
try:
    from snodo.core.interfaces import ValidatorResult
except ImportError:
    # Fallback for testing without full package
    from typing import Literal
    from pydantic import BaseModel

    class ValidatorResult(BaseModel):  # type: ignore[no-redef]
        """Output from a single validator."""
        validator_id: str
        severity: Literal["pass", "warn", "blocker", "error"]
        justification: str


class ShellError(Exception):
    """Raised when a shell operation fails."""


class CommandNotAllowedError(Exception):
    """Raised when attempting to execute non-whitelisted command."""


class ShellMCP:
    """MCP server for executing tests and converting results to ValidatorResult.
    
    Security model:
    - Whitelist of allowed test commands only
    - No arbitrary shell command execution
    - Sandboxed within project root
    """
    
    # Whitelist of allowed test commands
    ALLOWED_COMMANDS = {
        "pytest": [sys.executable, "-m", "pytest"],
        "npm": ["npm", "test"],
        "cargo": ["cargo", "test"],
    }
    
    def __init__(self, project_root: str, validator_id: str = "test_runner"):
        """Initialize shell MCP with project root.
        
        Args:
            project_root: Absolute path to project root directory
            validator_id: Identifier for validator results
        """
        self.project_root = Path(project_root).resolve()
        self.validator_id = validator_id
        
        # Ensure project root exists
        if not self.project_root.exists():
            raise ValueError(f"Project root does not exist: {self.project_root}")
        
        if not self.project_root.is_dir():
            raise ValueError(f"Project root is not a directory: {self.project_root}")
    
    def _validate_command(self, command_type: str) -> List[str]:
        """Validate and return command from whitelist.
        
        Args:
            command_type: Type of test command (pytest, npm, cargo)
            
        Returns:
            Command as list
            
        Raises:
            CommandNotAllowedError: If command not in whitelist
        """
        if command_type not in self.ALLOWED_COMMANDS:
            raise CommandNotAllowedError(
                f"Command '{command_type}' not allowed. "
                f"Allowed: {list(self.ALLOWED_COMMANDS.keys())}"
            )
        
        return self.ALLOWED_COMMANDS[command_type].copy()
    
    def run_tests(
        self,
        test_path: str,
        command_type: str = "pytest",
        extra_args: Optional[List[str]] = None
    ) -> ValidatorResult:
        """Run tests and return ValidatorResult.
        
        Args:
            test_path: Path to test file or directory (relative to project root)
            command_type: Type of test command to run
            extra_args: Additional arguments to pass to test command
            
        Returns:
            ValidatorResult with test results
            
        Raises:
            CommandNotAllowedError: If command not in whitelist
            ShellError: If test execution fails unexpectedly
        """
        # Validate command
        command = self._validate_command(command_type)
        
        # Build full command
        if extra_args:
            command.extend(extra_args)
        command.append(test_path)
        
        # Execute tests
        try:
            result = subprocess.run(
                command,
                cwd=str(self.project_root),
                capture_output=True,
                text=True,
                timeout=300  # 5 minute timeout
            )
        except subprocess.TimeoutExpired:
            return ValidatorResult(
                validator_id=self.validator_id,
                severity="blocker",
                justification="Tests timed out after 5 minutes"
            )
        except FileNotFoundError:
            return ValidatorResult(
                validator_id=self.validator_id,
                severity="blocker",
                justification=f"Test command '{command[0]}' not found. Install it first."
            )
        
        # Parse output and convert to ValidatorResult
        return self.parse_output(result.returncode, result.stdout, result.stderr)
    
    def parse_output(
        self,
        exit_code: int,
        stdout: str,
        stderr: str
    ) -> ValidatorResult:
        """Parse test output and convert to ValidatorResult.
        
        Args:
            exit_code: Process exit code (0 = success)
            stdout: Standard output from test run
            stderr: Standard error from test run
            
        Returns:
            ValidatorResult with appropriate severity
        """
        # Exit code 0 = all tests passed
        if exit_code == 0:
            # Check for warnings in output
            if self._has_warnings(stdout, stderr):
                return ValidatorResult(
                    validator_id=self.validator_id,
                    severity="warn",
                    justification=self._extract_summary(stdout, stderr)
                )
            else:
                return ValidatorResult(
                    validator_id=self.validator_id,
                    severity="pass",
                    justification=self._extract_summary(stdout, stderr)
                )
        
        # Exit code 5 = no tests collected (pytest specific)
        elif exit_code == 5:
            return ValidatorResult(
                validator_id=self.validator_id,
                severity="blocker",
                justification="No tests found in specified path"
            )
        
        # Any other non-zero exit code = tests failed
        else:
            return ValidatorResult(
                validator_id=self.validator_id,
                severity="blocker",
                justification=self._extract_failure_info(stdout, stderr, exit_code)
            )
    
    def _has_warnings(self, stdout: str, stderr: str) -> bool:
        """Check if output contains warnings.
        
        Args:
            stdout: Standard output
            stderr: Standard error
            
        Returns:
            True if warnings present
        """
        combined = stdout + stderr
        warning_patterns = [
            r"\d+ warning",
            r"DeprecationWarning",
            r"PendingDeprecationWarning",
            r"FutureWarning",
        ]
        
        for pattern in warning_patterns:
            if re.search(pattern, combined, re.IGNORECASE):
                return True
        
        return False
    
    def _extract_summary(self, stdout: str, stderr: str) -> str:
        """Extract test summary from output.
        
        Args:
            stdout: Standard output
            stderr: Standard error
            
        Returns:
            Summary string
        """
        # Look for pytest summary line (match the full line between ===)
        pytest_summary = re.search(
            r"=+ (.+?) =+",
            stdout,
            re.IGNORECASE
        )
        if pytest_summary:
            return pytest_summary.group(1).strip()
        
        # Look for npm test summary
        if "Tests:" in stdout:
            for line in stdout.split("\n"):
                if line.strip().startswith("Tests:"):
                    return line.strip()
        
        # Look for cargo test summary
        if "test result:" in stdout:
            for line in stdout.split("\n"):
                if "test result:" in line:
                    return line.strip()
        
        # Fallback: return last non-empty line
        for line in reversed((stdout + stderr).split("\n")):
            if line.strip():
                return line.strip()[:200]  # Limit length
        
        return "Tests completed"
    
    def _extract_failure_info(
        self,
        stdout: str,
        stderr: str,
        exit_code: int
    ) -> str:
        """Extract failure information from output.
        
        Args:
            stdout: Standard output
            stderr: Standard error
            exit_code: Exit code
            
        Returns:
            Failure description
        """
        # Look for pytest failure summary
        failed_match = re.search(
            r"(\d+) failed",
            stdout,
            re.IGNORECASE
        )
        
        if failed_match:
            num_failed = failed_match.group(1)
            # Try to extract test names
            test_names = re.findall(
                r"FAILED (.*?) -",
                stdout
            )
            if test_names:
                return f"{num_failed} test(s) failed: {', '.join(test_names[:5])}"
            else:
                return f"{num_failed} test(s) failed"
        
        # Look for error in stderr
        if stderr:
            # Get first error line
            for line in stderr.split("\n"):
                if line.strip() and any(err in line.lower() for err in ["error", "fail", "exception"]):
                    return f"Test error: {line.strip()[:200]}"
        
        # Fallback
        return f"Tests failed with exit code {exit_code}"


# Module-level instance for convenience
_shell_instance: Optional[ShellMCP] = None


def get_shell(
    project_root: Optional[str] = None,
    validator_id: str = "test_runner"
) -> ShellMCP:
    """Get shell MCP instance.
    
    Args:
        project_root: Project root directory (uses existing instance if None)
        validator_id: Validator ID for results
        
    Returns:
        ShellMCP instance
    """
    global _shell_instance
    
    if project_root is not None:
        _shell_instance = ShellMCP(project_root, validator_id)
    
    if _shell_instance is None:
        raise ValueError("Shell MCP not initialized. Call with project_root first.")
    
    return _shell_instance

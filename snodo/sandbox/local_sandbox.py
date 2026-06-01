"""Local sandbox - No isolation, direct execution.

FILE: snodo/sandbox/local_sandbox.py (Task 5.4)

Fallback sandbox that executes tasks directly on the host.
Provides the same interface as DockerSandbox but without isolation.
"""

import subprocess
import time
from pathlib import Path
from typing import Optional

from snodo.sandbox.base import Sandbox, SandboxConfig, SandboxResult, SandboxError


class LocalSandbox(Sandbox):
    """Local (non-isolated) sandbox for task execution.

    Runs commands directly as subprocesses on the host machine.
    This is the default when Docker is unavailable.
    """

    def __init__(self, **kwargs):
        pass

    def is_available(self) -> bool:
        """Local sandbox is always available."""
        return True

    def run_task(
        self,
        command: list,
        workspace: Path,
        config: Optional[SandboxConfig] = None,
    ) -> SandboxResult:
        """Run a task as a local subprocess.

        Args:
            command: Command and arguments to execute
            workspace: Working directory for execution
            config: Configuration (timeout used, other fields ignored)

        Returns:
            SandboxResult with execution output

        Raises:
            SandboxError: If subprocess creation fails
        """
        if config is None:
            config = SandboxConfig()

        timeout = config.timeout
        workspace_str = str(workspace.resolve())

        # Build environment with any extras from config
        import os
        env = dict(os.environ)
        if config.env:
            env.update(config.env)

        start_time = time.time()

        try:
            proc = subprocess.run(
                command,
                cwd=workspace_str,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
            )
            duration = time.time() - start_time

            return SandboxResult(
                exit_code=proc.returncode,
                stdout=proc.stdout,
                stderr=proc.stderr,
                duration=duration,
                sandbox_type="local",
            )

        except subprocess.TimeoutExpired as e:
            duration = time.time() - start_time
            return SandboxResult(
                exit_code=124,  # Standard timeout exit code
                stdout=str(e.stdout or ""),
                stderr=f"Command timed out after {timeout}s",
                duration=duration,
                sandbox_type="local",
            )

        except FileNotFoundError:
            raise SandboxError(f"Command not found: {command[0]}")

        except OSError as e:
            raise SandboxError(f"Failed to execute command: {e}")

    def cleanup(self) -> None:
        """No cleanup needed for local sandbox."""
        pass

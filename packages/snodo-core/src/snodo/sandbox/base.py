"""Sandbox base class - Abstract interface for task execution environments.

FILE: snodo/sandbox/base.py (Task 5.4)
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


class SandboxError(Exception):
    """Raised when sandbox operations fail."""
    pass


@dataclass
class SandboxResult:
    """Result of a sandboxed task execution.

    Attributes:
        exit_code: Process exit code (0 = success)
        stdout: Standard output captured from execution
        stderr: Standard error captured from execution
        duration: Execution duration in seconds
        sandbox_type: Type of sandbox used ("docker" or "local")
        container_id: Docker container ID (if docker sandbox)
    """
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    duration: float = 0.0
    sandbox_type: str = "local"
    container_id: Optional[str] = None


@dataclass
class SandboxConfig:
    """Configuration for sandbox execution.

    Attributes:
        image: Docker image name
        network: Network mode (none, bridge, host)
        memory_limit: Memory limit (e.g., "2g")
        cpu_limit: CPU quota (number of CPUs, e.g., 2.0)
        timeout: Maximum execution time in seconds
        env: Environment variables to pass
        mounts: Additional mounts {host_path: {bind: container_path, mode: rw/ro}}
    """
    image: str = "snodo-worker:latest"
    network: str = "none"
    memory_limit: str = "2g"
    cpu_limit: float = 2.0
    timeout: Optional[float] = None
    env: dict = field(default_factory=dict)
    mounts: dict = field(default_factory=dict)


class Sandbox(ABC):
    """Abstract base class for task execution sandboxes."""

    @abstractmethod
    def run_task(
        self,
        command: list,
        workspace: Path,
        config: Optional[SandboxConfig] = None,
    ) -> SandboxResult:
        """Run a task command in the sandbox.

        Args:
            command: Command and arguments to execute (e.g., ["snodo", "run", "task"])
            workspace: Project workspace directory to mount/use
            config: Optional sandbox configuration overrides

        Returns:
            SandboxResult with exit code, stdout, stderr

        Raises:
            SandboxError: If execution fails due to sandbox issues
        """
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """Check if this sandbox type is available.

        Returns:
            True if the sandbox can execute tasks
        """
        pass

    @abstractmethod
    def cleanup(self) -> None:
        """Clean up any resources held by the sandbox."""
        pass

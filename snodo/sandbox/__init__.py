"""Snodo Sandbox - Isolated task execution.

FILE: snodo/sandbox/__init__.py (Task 5.4)

Provides sandboxed execution environments for tasks.
Docker sandbox for isolation, local sandbox as fallback.
"""

from snodo.sandbox.base import Sandbox, SandboxResult, SandboxConfig, SandboxError
from snodo.sandbox.docker_sandbox import DockerSandbox
from snodo.sandbox.local_sandbox import LocalSandbox

__all__ = ["Sandbox", "SandboxResult", "SandboxConfig", "SandboxError", "DockerSandbox", "LocalSandbox"]


def create_sandbox(sandbox_type: str = "local", **kwargs) -> "Sandbox":
    """Factory to create the appropriate sandbox.

    Args:
        sandbox_type: "docker" or "local"
        **kwargs: Passed to sandbox constructor

    Returns:
        Sandbox instance

    Raises:
        SandboxError: If sandbox type is invalid or unavailable
    """
    if sandbox_type == "local":
        return LocalSandbox(**kwargs)
    elif sandbox_type == "docker":
        sandbox = DockerSandbox(**kwargs)
        if not sandbox.is_available():
            raise SandboxError(
                "Docker is not available. Install Docker or use --sandbox local"
            )
        return sandbox
    else:
        raise SandboxError(f"Unknown sandbox type: {sandbox_type}")

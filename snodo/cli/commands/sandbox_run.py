"""Sandbox execution helpers for the snodo run command.

Extracted from cli/commands/run_cmd.py to isolate sandbox logic.
"""

import sys
from pathlib import Path

from snodo.config import ConfigManager


def _build_sandbox_command(args) -> list:
    """Build the snodo run command for inside the container."""
    command = ["snodo", "run", args.description, "--protocol", args.protocol]
    if args.model:
        command.extend(["--model", args.model])
    if getattr(args, "coder", None):
        command.extend(["--coder", args.coder])
    if getattr(args, "mode", None):
        command.extend(["--mode", args.mode])
    if getattr(args, "mock", False):
        command.append("--mock")
    if getattr(args, "verbose", False):
        command.append("--verbose")
    from_pr = getattr(args, "from_pr", None)
    if from_pr:
        command.extend(["--from-pr", str(from_pr)])
    return command


def _build_sandbox_env(mgr: ConfigManager, model: str) -> dict:
    """Build environment variables (API keys) for the sandbox container."""
    env: dict[str, str] = {}
    api_key = mgr.get_key_for_model(model)
    if not api_key:
        return env
    provider = ConfigManager._provider_for_model(model)
    if provider:
        from snodo.infrastructure.config import DEFAULT_PROVIDER_CATALOG
        pc = DEFAULT_PROVIDER_CATALOG.get(provider)
        if pc and pc.api_key_env:
            env[pc.api_key_env] = api_key
    return env


def _print_sandbox_result(result, sandbox_image: str, config) -> None:
    """Print sandbox execution output and summary."""
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    print()
    print(f"Container: {result.container_id or 'N/A'}")
    print(f"Duration: {result.duration:.1f}s")
    print(f"Exit code: {result.exit_code}")


def _run_in_sandbox(args) -> int:
    """Execute task inside a Docker sandbox container.

    Builds the snodo run command and dispatches to DockerSandbox.
    Falls back to local execution if Docker is unavailable.
    """
    from snodo.sandbox import DockerSandbox, SandboxConfig, SandboxError
    from snodo.cli.commands.run_cmd import run_command

    sandbox = DockerSandbox()

    if not sandbox.is_available():
        print("Warning: Docker not available, falling back to local execution",
              file=sys.stderr)
        import dataclasses
        if dataclasses.is_dataclass(args):
            args = dataclasses.replace(args, sandbox="local")
        else:
            args.sandbox = "local"
        return run_command(args)

    if not sandbox.image_exists():
        print("Error: snodo-worker image not built", file=sys.stderr)
        print("Run: snodo sandbox build", file=sys.stderr)
        return 1

    mgr = ConfigManager()
    model = args.model or mgr.get_model()

    config = SandboxConfig(
        network="none",
        memory_limit="2g",
        cpu_limit=2.0,
        env=_build_sandbox_env(mgr, model),
    )

    print("Running in Docker sandbox...")
    print(f"  Image: {sandbox._image}")
    print(f"  Network: {config.network}")
    print(f"  Memory: {config.memory_limit}")
    print(f"  CPUs: {config.cpu_limit}")
    print()

    command = _build_sandbox_command(args)
    from snodo.infrastructure.paths import require_project_root
    project_root = Path(require_project_root())
    try:
        result = sandbox.run_task(command, project_root, config=config)
    except SandboxError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    _print_sandbox_result(result, sandbox._image, config)
    return result.exit_code

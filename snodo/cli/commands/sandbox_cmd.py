"""Sandbox command - Manage Docker sandbox for isolated execution.

FILE: snodo/cli/commands/sandbox_cmd.py (Task 5.4)
"""

import sys
from pathlib import Path


def sandbox_command(args) -> int:
    """Dispatch sandbox subcommands."""
    action = args.sandbox_action

    if action == "build":
        return _sandbox_build(args)
    elif action == "status":
        return _sandbox_status(args)
    else:
        print(f"Error: Unknown sandbox action: {action}", file=sys.stderr)
        return 1


def _sandbox_build(args) -> int:
    """Build the snodo-worker Docker image."""
    from snodo.sandbox import DockerSandbox, SandboxError
    from snodo.infrastructure.paths import resolve_project_root

    # Find Dockerfile.worker
    project_root = Path(resolve_project_root() or Path.cwd())
    dockerfile = project_root / "Dockerfile.worker"

    if not dockerfile.exists():
        # Check in package directory as fallback
        pkg_dir = Path(__file__).parent.parent.parent
        dockerfile = pkg_dir / "Dockerfile.worker"

    if not dockerfile.exists():
        print("Error: Dockerfile.worker not found", file=sys.stderr)
        print("Expected at project root or snodo package root.", file=sys.stderr)
        return 1

    tag = getattr(args, "tag", None) or "snodo-worker:latest"

    sandbox = DockerSandbox(image=tag)
    if not sandbox.is_available():
        print("Error: Docker is not available", file=sys.stderr)
        print("Make sure Docker Desktop is running.", file=sys.stderr)
        return 1

    print(f"Building image: {tag}")
    print(f"Dockerfile: {dockerfile}")
    print()

    try:
        image_id = sandbox.build_image(dockerfile, tag=tag)
        print(f"Successfully built: {tag}")
        print(f"Image ID: {image_id[:12]}")
        return 0
    except SandboxError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def _sandbox_status(args) -> int:
    """Check Docker availability and image status."""
    print("Sandbox Status")
    print("=" * 40)

    # Check Docker availability
    try:
        from snodo.sandbox import DockerSandbox
        sandbox = DockerSandbox()
        docker_available = sandbox.is_available()
    except Exception:
        docker_available = False

    if docker_available:
        print("  Docker:  available")

        # Check image
        if sandbox.image_exists():
            print("  Image:   snodo-worker:latest (ready)")
        else:
            print("  Image:   snodo-worker:latest (not built)")
            print("           Run: snodo sandbox build")

        # Get Docker info
        try:
            info = sandbox.client.info()
            print(f"  Runtime: {info.get('ServerVersion', 'unknown')}")
            print(f"  OS:      {info.get('OperatingSystem', 'unknown')}")
        except Exception:
            pass
    else:
        print("  Docker:  not available")
        print("           Install Docker Desktop or start the daemon.")
        print()
        print("  Fallback: local sandbox (no isolation)")

    print()
    print("Sandbox types:")
    print("  local   - Direct execution (no isolation, always available)")
    print("  docker  - Container isolation (requires Docker)")
    return 0

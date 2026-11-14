"""Build commands and load task text for the demo fixture."""

from pathlib import Path


def command_prefix(version: str | None = None) -> list[str]:
    """Return the executable prefix, optionally pinned to a Snodo version."""
    if version is None:
        return ["snodo"]
    return ["uvx", f"snodo@{version}"]


def run_command(
    task_text: str,
    protocol_path: str | Path,
    model: str,
    coder: str | None = None,
    version: str | None = None,
) -> list[str]:
    """Build the argument vector for running the demo task."""
    command = command_prefix(version) + [
        "run",
        task_text,
        "--protocol",
        str(protocol_path),
        "--model",
        model,
        "--retain-worktree",
    ]
    if coder is not None:
        command.extend(["--coder", coder])
    return command


def read_task_text() -> str:
    """Read the fixture task exactly, preserving all newline sequences."""
    task_path = Path(__file__).parent / "scikit-learn-26194" / "task.md"
    return task_path.read_bytes().decode("utf-8")

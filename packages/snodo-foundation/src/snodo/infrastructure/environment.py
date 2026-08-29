"""Environment preparation for task execution in git worktrees.

FILE: snodo/infrastructure/environment.py

Detects project ecosystems from lockfile markers (npm, uv/pip, cargo, go) or
reads protocol-declared commands (execution.prepare_command) and executes
environment setup before task execution/validation.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Callable, Any, Tuple, List

from snodo.compiler.models import Protocol


class EnvironmentPrepError(Exception):
    """Raised when environment preparation fails (an operational fault)."""

    def __init__(self, command: str, exit_code: int, output: str):
        self.command = command
        self.exit_code = exit_code
        self.output = output
        super().__init__(
            f"Environment preparation failed (exit {exit_code}) running '{command}':\n{output}"
        )


@dataclass
class PrepResult:
    """Result of an environment preparation step."""
    status: str  # "executed" | "skipped"
    command: Optional[str] = None
    reason: Optional[str] = None
    exit_code: int = 0
    output: str = ""


# Ecosystem marker rules: list of (marker_files, installed_dir, default_command)
# Checked in order.
ECOSYSTEM_RULES: List[Tuple[List[str], str, str]] = [
    # Node.js
    (["package-lock.json"], "node_modules", "npm ci"),
    (["pnpm-lock.yaml"], "node_modules", "pnpm install --frozen-lockfile"),
    (["yarn.lock"], "node_modules", "yarn install --frozen-lockfile"),
    (["bun.lockb", "bun.lock"], "node_modules", "bun install"),
    (["package.json"], "node_modules", "npm install"),

    # Python (uv / pip)
    (["uv.lock"], ".venv", "uv sync"),
    (["pyproject.toml"], ".venv", "uv sync"),
    (["requirements.txt"], ".venv", "pip install -r requirements.txt"),

    # Cargo (Rust)
    (["Cargo.lock", "Cargo.toml"], "target", "cargo fetch"),

    # Go
    (["go.sum", "go.mod"], "vendor", "go mod download"),
]


def detect_prepare_command(target_dir: str | Path) -> Optional[Tuple[str, str]]:
    """Detect ecosystem prepare command and installed guard directory.

    Args:
        target_dir: Path to directory containing project files.

    Returns:
        (command, installed_guard_dir) if markers match, or None if no markers match.
    """
    root = Path(target_dir)
    if not root.is_dir():
        return None

    for markers, guard_dir, cmd in ECOSYSTEM_RULES:
        if any((root / marker).exists() for marker in markers):
            return cmd, guard_dir

    return None


def prepare_environment(
    target_dir: str | Path,
    protocol: Optional[Protocol] = None,
    explicit_command: Optional[str] = None,
    run_command_fn: Optional[Callable[[str, str], Tuple[int, str]]] = None,
    shell_mcp: Any = None,
) -> PrepResult:
    """Prepare environment dependencies in *target_dir* before task execution.

    Args:
        target_dir: Absolute path to the worktree or project root directory.
        protocol: Optional protocol containing execution.prepare_command configuration.
        explicit_command: Direct override command (takes precedence over protocol).
        run_command_fn: Optional custom shell runner `fn(cmd, cwd) -> (exit_code, output)`.
            Used for injection in tests without real network/shell execution.
        shell_mcp: Optional ShellMCP instance to run commands.

    Returns:
        PrepResult indicating whether preparation was executed or skipped.

    Raises:
        EnvironmentPrepError: If the preparation command fails (exit != 0).
    """
    target_path = Path(target_dir)

    # 1. Resolve command string (explicit arg > protocol config > auto-detect)
    command_to_run: Optional[str] = None
    guard_dir: Optional[str] = None

    if explicit_command is not None:
        cmd_strip = explicit_command.strip()
        if cmd_strip in ("", "none", "NONE"):
            return PrepResult(status="skipped", reason="explicitly disabled")
        command_to_run = cmd_strip
    elif protocol and protocol.execution.prepare_command is not None:
        cmd_strip = protocol.execution.prepare_command.strip()
        if cmd_strip in ("", "none", "NONE"):
            return PrepResult(status="skipped", reason="explicitly disabled in protocol")
        command_to_run = cmd_strip
    else:
        detection = detect_prepare_command(target_path)
        if not detection:
            return PrepResult(status="skipped", reason="no recognized markers")
        command_to_run, guard_dir = detection

    # 2. Skip auto-install if dependencies already exist
    if guard_dir and (target_path / guard_dir).is_dir():
        return PrepResult(
            status="skipped",
            command=command_to_run,
            reason=f"already installed ({guard_dir} exists)",
        )

    # 3. Execute command
    exit_code = 0
    output = ""

    if run_command_fn is not None:
        exit_code, output = run_command_fn(command_to_run, str(target_path))
    elif shell_mcp is not None and hasattr(shell_mcp, "run_command"):
        try:
            res = shell_mcp.run_command(command_to_run, cwd=str(target_path))
            # res may be an object or tuple depending on ShellMCP API
            if hasattr(res, "exit_code"):
                exit_code = res.exit_code
                output = getattr(res, "stdout", "") + getattr(res, "stderr", "")
            elif isinstance(res, tuple):
                exit_code, output = res[0], res[1]
            else:
                exit_code = 0
                output = str(res)
        except Exception as exc:
            exit_code = 1
            output = str(exc)
    else:
        import subprocess
        proc = subprocess.run(  # noqa: S602 - operator/protocol-authored shell string (explicit_command, protocol.execution.prepare_command, or the literal ECOSYSTEM_RULES defaults such as "npm ci" / "uv sync"); compound commands require a shell. Trusted-repo input per ADR 014.
            command_to_run,
            shell=True,
            cwd=str(target_path),
            capture_output=True,
            text=True,
        )
        exit_code = proc.returncode
        output = proc.stdout + proc.stderr

    if exit_code != 0:
        raise EnvironmentPrepError(command_to_run, exit_code, output)

    return PrepResult(
        status="executed",
        command=command_to_run,
        exit_code=exit_code,
        output=output,
    )

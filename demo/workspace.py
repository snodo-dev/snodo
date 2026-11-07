"""Prepare an isolated checkout for the recorded demo instance."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import Any, Callable, Sequence


CommandRunner = Callable[..., Any]
_REQUIRED_INSTANCE_KEYS = ("instance_id", "repo_url", "base_commit")


def _run_command(
    command: Sequence[str], *, cwd: str | Path | None = None
) -> subprocess.CompletedProcess[str]:
    """Run an external command, raising when it fails."""
    return subprocess.run(  # noqa: S603
        list(command), cwd=cwd, check=True, capture_output=True, text=True
    )


def load_instance(path: str | Path) -> dict[str, Any]:
    """Load an instance JSON file and ensure its checkout fields are present."""
    with Path(path).open(encoding="utf-8") as instance_file:
        instance = json.load(instance_file)

    if not isinstance(instance, dict):
        raise ValueError("instance file must contain a JSON object")
    for key in _REQUIRED_INSTANCE_KEYS:
        if key not in instance:
            raise ValueError(f"instance file is missing required key: {key}")
    return instance


def clone_instance(
    instance: dict[str, Any],
    target: str | Path,
    *,
    runner: CommandRunner = _run_command,
) -> Path:
    """Clone an instance repository and check out its recorded commit detached."""
    destination = Path(target)
    if destination.exists() and any(destination.iterdir()):
        raise FileExistsError(f"target directory is not empty: {destination}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    runner(["git", "clone", str(instance["repo_url"]), str(destination)])
    runner(
        ["git", "checkout", "--detach", str(instance["base_commit"])],
        cwd=destination,
    )
    return destination


def initialize_workspace(
    workspace: str | Path,
    command_prefix: Sequence[str],
    *,
    runner: CommandRunner = _run_command,
) -> None:
    """Initialize Snodo in *workspace* with the shipped bugfix-surgeon template."""
    runner(
        [*command_prefix, "init", "--template", "bugfix-surgeon", "--yes"],
        cwd=workspace,
    )

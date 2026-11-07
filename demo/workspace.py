"""Prepare an isolated checkout for a demo task."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import Any, Callable, Sequence


CommandRunner = Callable[..., Any]
REQUIRED_INSTANCE_KEYS = ("instance_id", "repo_url", "base_commit")


def _run_command(
    runner: CommandRunner,
    command: Sequence[str],
    *,
    cwd: str | Path | None = None,
) -> Any:
    """Run an external command through the caller-provided runner."""
    return runner(list(command), cwd=cwd, check=True, capture_output=True, text=True)


def load_instance(path: str | Path) -> dict[str, Any]:
    """Load an instance JSON file and check its required fields."""
    with Path(path).open(encoding="utf-8") as instance_file:
        instance = json.load(instance_file)
    if not isinstance(instance, dict):
        raise ValueError("instance file must contain a JSON object")
    for key in REQUIRED_INSTANCE_KEYS:
        if key not in instance:
            raise ValueError(f"instance file is missing required key: {key}")
    return instance


def clone_instance(
    instance: dict[str, Any],
    target: str | Path,
    *,
    runner: CommandRunner = subprocess.run,
) -> Path:
    """Clone an instance repository and check out its recorded commit."""
    destination = Path(target)
    if destination.exists() and any(destination.iterdir()):
        raise FileExistsError(f"target folder is not empty: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    _run_command(runner, ["git", "clone", str(instance["repo_url"]), str(destination)])
    _run_command(
        runner,
        ["git", "checkout", "--detach", str(instance["base_commit"])],
        cwd=destination,
    )
    return destination


def init_workspace(
    target: str | Path,
    command_prefix: Sequence[str],
    *,
    runner: CommandRunner = subprocess.run,
) -> None:
    """Initialize Snodo in a clone using the shipped bugfix-surgeon template."""
    _run_command(
        runner,
        [*command_prefix, "init", "--template", "bugfix-surgeon", "--yes"],
        cwd=target,
    )

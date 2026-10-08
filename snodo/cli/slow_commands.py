"""Registration-time wave indicator wiring for silent CLI commands."""

from functools import wraps

import typer


_LABELS = {
    "ready": "checking readiness",
    "readiness": "checking readiness",
    "validate": "validating changes",
    "config test": "testing providers",
    "config validate": "validating configuration",
    "protocol validate": "validating protocol",
    "recon": "researching codebase",
    "run": "running task",
    "plan run": "running plan",
    "task list": "loading tasks",
    "plan list": "loading plans",
    "queue list": "loading queue",
    "audit verify": "reading the audit log",
    "cloud status": "checking cloud status",
    "worktree list": "loading worktrees",
    "survey": "loading survey",
}

_EXCLUSIONS = {
    "dashboard": "full-screen terminal UI",
    "serve": "long-running server",
}


def _default_label(full_name: str) -> str:
    """Create a readable activity label for commands without a curated label."""
    return f"working on {full_name}"


def add_slow_command_indicators(command_app: typer.Typer, prefix: str = "") -> None:
    """Wrap every registered callback except commands that own the terminal.

    The output-aware context stops and clears the wave before the first real
    write, including a prompt. Registration-time wiring avoids scattering UI
    concerns through command implementations. Progress-streaming commands are
    safe because the wave clears as soon as their first progress line is written.
    """
    from snodo.cli.spinner import wave_while_silent

    for info in command_app.registered_commands:
        name = info.name or typer.main.get_command_name(info.callback.__name__)
        full_name = f"{prefix} {name}".strip()
        if full_name in _EXCLUSIONS:
            continue
        callback = info.callback
        label = _LABELS.get(full_name, _default_label(full_name))

        @wraps(callback)
        def wrapped(*args, __callback=callback, __label=label, **kwargs):
            with wave_while_silent(__label):
                return __callback(*args, **kwargs)

        wrapped.__snodo_wave_wrapped__ = True
        info.callback = wrapped
    for group in command_app.registered_groups:
        group_name = group.name or ""
        add_slow_command_indicators(
            group.typer_instance, f"{prefix} {group_name}".strip(),
        )

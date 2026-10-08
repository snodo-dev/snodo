"""Registration-time wave indicator wiring for silent, slow CLI commands."""

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
}


def add_slow_command_indicators(command_app: typer.Typer, prefix: str = "") -> None:
    """Wrap selected callbacks centrally, leaving progress-streaming commands alone.

    The output-aware context stops and clears the wave before the first real
    write, including a prompt. Registration-time wiring avoids scattering UI
    concerns through command implementations; run and plan-run callbacks are
    deliberately absent because they already stream their own progress.
    """
    from snodo.cli.spinner import wave_while_silent

    for info in command_app.registered_commands:
        name = info.name or typer.main.get_command_name(info.callback.__name__)
        full_name = f"{prefix} {name}".strip()
        label = _LABELS.get(full_name)
        if full_name == "models" and info.callback.__name__ == "models":
            callback = info.callback

            @wraps(callback)
            def models_wrapped(*args, __callback=callback, **kwargs):
                if not kwargs.get("check"):
                    return __callback(*args, **kwargs)
                with wave_while_silent("testing providers"):
                    return __callback(*args, **kwargs)

            info.callback = models_wrapped
        if label is None:
            continue
        callback = info.callback

        @wraps(callback)
        def wrapped(*args, __callback=callback, __label=label, **kwargs):
            with wave_while_silent(__label):
                return __callback(*args, **kwargs)

        info.callback = wrapped
    for group in command_app.registered_groups:
        group_name = group.name or ""
        add_slow_command_indicators(
            group.typer_instance, f"{prefix} {group_name}".strip(),
        )

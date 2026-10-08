"""Default wave-indicator coverage for registered CLI commands."""

import json

import typer
from typer.testing import CliRunner

from snodo.cli.main import app
from snodo.cli.slow_commands import _EXCLUSIONS


def _commands(command_app: typer.Typer, prefix: str = ""):
    for command in command_app.registered_commands:
        name = command.name or typer.main.get_command_name(
            command.callback.__name__,
        )
        yield f"{prefix} {name}".strip(), command
    for group in command_app.registered_groups:
        name = group.name or ""
        yield from _commands(
            group.typer_instance, f"{prefix} {name}".strip(),
        )


def test_every_registered_command_is_wrapped_or_explicitly_excluded():
    commands = dict(_commands(app))
    assert set(_EXCLUSIONS) <= set(commands)
    for name, command in commands.items():
        assert bool(getattr(command.callback, "__snodo_wave_wrapped__", False)) == (
            name not in _EXCLUSIONS
        ), name


def test_json_stdout_is_unchanged_for_wrapped_command():
    result = CliRunner().invoke(app, ["protocol", "schema", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)
    assert "[▁" not in result.stdout


def test_init_callback_uses_output_aware_wave_wrapper():
    """Init's interactive prompts use the same output-aware wrapper contract."""
    init = dict(_commands(app))["init"].callback
    assert getattr(init, "__snodo_wave_wrapped__", False)
    # test_wave_is_cleared_before_first_prompt_or_output exercises prompt-time
    # cleanup in the actual context manager used by this registration wrapper.

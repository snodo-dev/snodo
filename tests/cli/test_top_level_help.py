"""Contracts for top-level CLI help organization and the start-here screen."""

from typer.main import get_command
from typer.testing import CliRunner

from snodo.cli.main import HELP_PANELS, app


def test_top_level_commands_are_assigned_to_exactly_one_help_panel():
    cli = get_command(app)
    commands = {
        name: command
        for name, command in cli.commands.items()
        if not command.hidden
    }
    expected = set().union(*HELP_PANELS.values())

    assert set(commands) == expected
    for name, command in commands.items():
        matching_panels = [
            panel for panel, members in HELP_PANELS.items() if name in members
        ]
        assert len(matching_panels) == 1
        assert command.rich_help_panel == matching_panels[0]


def test_help_shows_all_five_panels():
    result = CliRunner().invoke(app, ["--help"], terminal_width=120)

    assert result.exit_code == 0
    for panel in ("Daily loop", "Work records", "Governance", "Setup", "Advanced"):
        assert panel in result.stdout


def test_bare_cli_prints_start_here_screen():
    result = CliRunner().invoke(app, [])

    assert result.exit_code == 0
    for line in (
        "snodo init", "snodo ready", "snodo run", "snodo status", "snodo logs",
        "snodo --help", "snodo <command> --help",
    ):
        assert line in result.stdout

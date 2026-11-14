from pathlib import Path


def test_command_reference_lists_dashboard_modes() -> None:
    command_reference = Path(__file__).parents[1] / "docs" / "command-reference.md"
    dashboard_row = next(
        line for line in command_reference.read_text().splitlines()
        if line.startswith("| `snodo dashboard` |")
    )

    assert "`--web`" in dashboard_row
    assert "`--cli`" in dashboard_row

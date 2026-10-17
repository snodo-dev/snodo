"""Keep OpenCode launch flags aligned with the checked-in per-major help contracts."""

import re
from pathlib import Path
from unittest.mock import patch

import pytest
from snodo.coders.opencode_cli_adapter import OpenCodeCLIAdapter


FIXTURES = Path(__file__).parent / "fixtures"


def _help_flags(fixture: Path) -> set[str]:
    return set(re.findall(r"(?<!\w)--[a-z][a-z0-9-]*|(?<!\w)-[a-zA-Z]", fixture.read_text()))


@pytest.mark.parametrize(
    ("major", "fixture_name"),
    [(1, "opencode-1-run-help.txt"), (2, "opencode-2.0.32-run-help.txt")],
)
def test_launch_argv_flags_are_listed_in_recorded_run_help(tmp_path, major, fixture_name):
    fixture = FIXTURES / fixture_name
    adapter = OpenCodeCLIAdapter(model="", workspace=tmp_path)
    # Patch version detection: building argv must not spawn or inspect a real CLI.
    with patch.object(OpenCodeCLIAdapter, "_cli_major_version", return_value=major):
        argv = adapter._build_argv("contract prompt", str(tmp_path), "openai/test-model")

    launch_flags = {token for token in argv[2:] if token.startswith("-")}
    unsupported = launch_flags - _help_flags(fixture)
    assert not unsupported, (
        f"{fixture_name} does not list launch flag(s) {sorted(unsupported)}; "
        f"update/verify the recorded help fixture: {fixture}"
    )


def test_v2_recorded_help_explicitly_excludes_v1_launch_flags():
    fixture = FIXTURES / "opencode-2.0.32-run-help.txt"
    flags = _help_flags(fixture)
    assert "--dir" not in flags, f"Unexpected --dir in recorded help fixture: {fixture}"
    assert "--dangerously-skip-permissions" not in flags, (
        f"Unexpected --dangerously-skip-permissions in recorded help fixture: {fixture}"
    )

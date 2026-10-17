"""Recorded opencode run help contracts; never invoke a host CLI in the gate."""

import json
import re
from pathlib import Path
from unittest.mock import patch

import pytest

from snodo.coders.opencode_cli_adapter import OpenCodeCLIAdapter

# Recorded `opencode run --help` option lines for the supported generations.
# Keep these independent of the adapter: an unsupported flag must fail the gate.
RUN_HELP = {
    1: """Usage: opencode run [message..]
  --format <format>                 output format
  --dir <directory>                 project directory
  --dangerously-skip-permissions    skip permission checks
  -m, --model <model>               model to use
""",
    2: """Usage: opencode run [message..]
  --format <format>                 output format
  --standalone                      run a standalone server
  -m, --model <model>               model to use
  --attach <url>                    attach to a running server
""",
}


def _accepted_options(help_text: str) -> set[str]:
    return set(re.findall(r"(?<!\w)--[\w-]+|(?<!\w)-[a-zA-Z](?!\w)", help_text))


def _assert_supported_flags(flags: set[str], major: int) -> None:
    assert flags <= _accepted_options(RUN_HELP[major]), "launch flags absent from recorded run help"


@pytest.mark.parametrize("unsupported", ["--auto", "--dir", "--project", "--dangerously-skip-permissions"])
def test_recorded_help_rejects_unsupported_v2_flags(unsupported: str, tmp_path: Path) -> None:
    adapter = OpenCodeCLIAdapter(workspace=tmp_path)
    with patch.object(adapter, "_cli_major_version", return_value=2):
        argv = adapter._build_argv("do work", str(tmp_path), "")
    argv.append(unsupported)
    with pytest.raises(AssertionError, match="launch flags absent from recorded run help"):
        _assert_supported_flags({arg for arg in argv[2:] if arg.startswith("-")}, 2)


@pytest.mark.parametrize("major", [1, 2])
def test_launch_flags_match_recorded_run_help(major: int, tmp_path: Path) -> None:
    adapter = OpenCodeCLIAdapter(workspace=tmp_path, model="opencode-cli/test/model")
    with patch.object(adapter, "_cli_major_version", return_value=major):
        argv = adapter._build_argv("do work", str(tmp_path), adapter._bare_model())
    assert argv[:2] == ["opencode", "run"]
    flags = {arg for arg in argv[2:] if arg.startswith("-")}
    _assert_supported_flags(flags, major)
    assert argv[-2:] == ["-m", "test/model"]
    if major == 1:
        assert argv[argv.index("--dir") + 1] == str(tmp_path)
        assert "--dangerously-skip-permissions" in argv
    else:
        assert "--standalone" in argv
        assert "--attach" not in argv
        assert "--dir" not in argv  # 2.x uses cwd, not an invented directory flag.
        assert adapter._workspace == tmp_path


def test_workspace_edit_rules_allow_only_workspace(tmp_path: Path) -> None:
    adapter = OpenCodeCLIAdapter(workspace=tmp_path / "task")
    with patch("snodo.coders.opencode_cli_adapter.subprocess_env_for_coder", return_value={}):
        config = json.loads(adapter._subprocess_env()["OPENCODE_CONFIG_CONTENT"])
    rules = config["permission"]["edit"]
    # https://opencode.ai/docs/permissions/#granular-rules-object-syntax
    # OpenCode uses only * and ? wildcards (all other characters literal).
    # Its permission patterns use last matching rule; `edit: deny`
    # instead blocked edits everywhere, even in the task workspace.
    def decision(path: Path) -> str:
        return next((value for pattern, value in reversed(list(rules.items()))
                     if re.fullmatch(re.escape(pattern).replace(r"\*", ".*").replace(r"\?", "."), str(path))), "ask")

    assert decision((tmp_path / "task" / "src" / "main.py").resolve()) == "allow"
    assert decision((tmp_path / "other" / "main.py").resolve()) == "deny"
    assert decision((tmp_path / "task-sibling" / "main.py").resolve()) == "deny"
    assert decision((tmp_path / "task" / ".." / "outside.py").resolve()) == "deny"
    assert config["permission"]["external_directory"] == "deny"
    assert config["tools"]["session_move"] is False
    assert config["tools"]["worktree_create"] is False


@pytest.mark.parametrize("permission", ["allow", {"edit": "allow", "external_directory": "allow"}])
def test_inherited_permissions_cannot_replace_boundary(tmp_path: Path, permission) -> None:
    adapter = OpenCodeCLIAdapter(workspace=tmp_path)
    inherited = {"permission": permission, "tools": {"session_move": True, "worktree_create": True}}
    with patch("snodo.coders.opencode_cli_adapter.subprocess_env_for_coder", return_value={
        "OPENCODE_CONFIG_CONTENT": json.dumps(inherited),
    }):
        config = json.loads(adapter._subprocess_env()["OPENCODE_CONFIG_CONTENT"])
    assert config["permission"]["edit"] == {"*": "deny", str(tmp_path.resolve() / "**"): "allow"}
    assert config["permission"]["external_directory"] == "deny"
    assert config["tools"] == {"session_move": False, "worktree_create": False}

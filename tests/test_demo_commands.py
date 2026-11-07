"""Tests for demo command construction and task loading."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location(
    "demo_commands", ROOT / "demo" / "commands.py"
)
demo_commands = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(demo_commands)


def test_prefix_defaults_to_installed_snodo():
    assert demo_commands.command_prefix() == ["snodo"]


def test_prefix_pins_version_with_uvx():
    assert demo_commands.command_prefix("1.2.3") == ["uvx", "snodo@1.2.3"]


def test_run_command_carries_protocol_model_and_retain_worktree():
    assert demo_commands.run_command("do it", "protocol.yml", "vendor/model") == [
        "snodo",
        "run",
        "do it",
        "--protocol",
        "protocol.yml",
        "--model",
        "vendor/model",
        "--retain-worktree",
    ]


def test_task_text_is_one_argument_unchanged():
    task_text = 'first line "quoted"\r\nsecond line'
    command = demo_commands.run_command(task_text, "protocol.yml", "model")
    assert command[2] == task_text
    assert command.count(task_text) == 1


def test_coder_flag_only_when_given():
    without_coder = demo_commands.run_command("task", "protocol.yml", "model")
    with_coder = demo_commands.run_command(
        "task", "protocol.yml", "model", coder="opencode-cli"
    )
    assert "--coder" not in without_coder
    assert with_coder[-2:] == ["--coder", "opencode-cli"]


def test_task_text_read_without_newline_translation():
    task_path = ROOT / "demo" / "scikit-learn-26194" / "task.md"
    assert demo_commands.read_task_text() == task_path.read_bytes().decode("utf-8")

"""Operator-visible behavior for ``snodo mode`` commands."""

import json
from types import SimpleNamespace

import pytest

from snodo.infrastructure.state import ProjectState, read_state, write_state


@pytest.fixture
def mode_project(tmp_path, monkeypatch):
    root = tmp_path / "project"
    snodo_dir = root / ".snodo"
    snodo_dir.mkdir(parents=True)
    (snodo_dir / "protocol.yml").write_text(
        "protocol_id: mode-test\n"
        "name: Mode test\n"
        "modes:\n"
        "  - mode_id: plan\n"
        "    name: Planning\n"
        "    transitions: {planned: build}\n"
        "  - mode_id: build\n"
        "    name: Building\n"
        "    transitions: {}\n"
        "  - mode_id: release\n"
        "    name: Release\n"
        "    transitions: {}\n"
        "validators:\n"
        "  - validator_id: basic\n"
        "    validator_type: quality\n"
        "    evaluation_phase: post_execute\n"
        "    criteria: [passes]\n"
        "initial_mode: plan\n"
    )
    (tmp_path / "snodo-home").mkdir()
    monkeypatch.setenv("SNODO_HOME", str(tmp_path / "snodo-home"))
    return root


def test_mode_show_displays_current_mode(mode_project, monkeypatch, capsys):
    from snodo.cli.commands.mode_cmd import mode_command

    write_state(str(mode_project), ProjectState(current_mode="plan"))
    monkeypatch.setattr(
        "snodo.infrastructure.paths.require_project_root", lambda: str(mode_project)
    )

    assert mode_command(SimpleNamespace(mode_action="show")) == 0
    assert "Current mode: Planning (plan)" in capsys.readouterr().out


def test_mode_show_json_reports_current_mode(mode_project, monkeypatch, capsys):
    from snodo.cli.commands.mode_cmd import mode_command

    write_state(str(mode_project), ProjectState(current_mode="plan"))
    monkeypatch.setattr(
        "snodo.infrastructure.paths.require_project_root", lambda: str(mode_project)
    )

    assert mode_command(SimpleNamespace(mode_action="show", json=True)) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["mode"] == "plan"
    assert result["name"] == "Planning (plan)"
    assert result["ok"] is True


def test_mode_change_follows_declared_transition(mode_project, monkeypatch, capsys):
    from snodo.cli.commands.mode_cmd import mode_command

    write_state(str(mode_project), ProjectState(current_mode="plan"))
    monkeypatch.setattr(
        "snodo.infrastructure.paths.require_project_root", lambda: str(mode_project)
    )

    assert mode_command(
        SimpleNamespace(mode_action="change", new_mode="build")
    ) == 0
    assert read_state(str(mode_project)).current_mode == "build"
    assert "Mode changed to: Building (build)" in capsys.readouterr().out


def test_mode_change_rejects_undeclared_transition_without_changing_state(
    mode_project, monkeypatch, capsys
):
    from snodo.cli.commands.mode_cmd import mode_command

    write_state(str(mode_project), ProjectState(current_mode="plan"))
    monkeypatch.setattr(
        "snodo.infrastructure.paths.require_project_root", lambda: str(mode_project)
    )

    assert mode_command(
        SimpleNamespace(mode_action="change", new_mode="release")
    ) == 1
    assert read_state(str(mode_project)).current_mode == "plan"
    assert "Allowed transitions: build" in capsys.readouterr().err


def test_mode_change_rejects_mode_missing_from_protocol(mode_project, monkeypatch, capsys):
    from snodo.cli.commands.mode_cmd import mode_command

    write_state(str(mode_project), ProjectState(current_mode="plan"))
    monkeypatch.setattr(
        "snodo.infrastructure.paths.require_project_root", lambda: str(mode_project)
    )

    assert mode_command(
        SimpleNamespace(mode_action="change", new_mode="missing")
    ) == 1
    assert read_state(str(mode_project)).current_mode == "plan"
    assert "Mode 'missing' not found" in capsys.readouterr().err

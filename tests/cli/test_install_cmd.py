"""Tests for snodo install and uninstall CLI commands (install_cmd.py).

FILE: tests/cli/test_install_cmd.py
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import typer
from typer.testing import CliRunner

from snodo.cli.commands.install_cmd import (
    _audit_global,
    _uninstall_all_entries,
    _uninstall_orphans,
    _uninstall_purge,
    install_command,
    register,
    uninstall_command,
)


@pytest.fixture
def temp_project(tmp_path):
    """Set up a temporary project directory with a valid protocol.yml."""
    snodo_dir = tmp_path / ".snodo"
    snodo_dir.mkdir(parents=True, exist_ok=True)
    protocol_file = snodo_dir / "protocol.yml"
    protocol_file.write_text(
        """
protocol_id: "test-proto"
name: "Test Protocol"
version: "1.0.0"
initial_mode: "dev"
modes:
  - mode_id: "dev"
    name: "Developer"
    description: "Dev mode"
validators:
  - validator_id: "val1"
    validator_type: "quality"
    evaluation_phase: "post_execute"
"""
    )
    return tmp_path


# ============================================================================
# 1. install_command tests
# ============================================================================

def test_install_command_happy_path(temp_project, monkeypatch, capsys):
    """install_command installs MCP entries and returns 0."""
    monkeypatch.setattr("snodo.infrastructure.paths.require_project_root", lambda: str(temp_project))
    mock_install = MagicMock(return_value=(["snodo-dev"], []))
    monkeypatch.setattr("snodo.cli.commands.install_cmd.install", mock_install)
    monkeypatch.setattr("snodo.cli.commands.install_cmd.get_claude_config_path", lambda: temp_project / "claude.json")
    monkeypatch.setattr("snodo.cli.commands.install_cmd._audit_global", MagicMock())

    args = SimpleNamespace(protocol=".snodo/protocol.yml")
    res = install_command(args)

    assert res == 0
    assert mock_install.called
    out = capsys.readouterr().out
    assert "Installed" in out or "Claude" in out or "snodo" in out


def test_install_command_protocol_missing(temp_project, monkeypatch, capsys):
    """install_command returns 1 when protocol.yml does not exist."""
    monkeypatch.setattr("snodo.infrastructure.paths.require_project_root", lambda: str(temp_project))

    args = SimpleNamespace(protocol=".snodo/nonexistent.yml")
    res = install_command(args)

    assert res == 1
    err = capsys.readouterr().err
    assert "Protocol file not found" in err


def test_install_command_protocol_load_failure(temp_project, monkeypatch, capsys):
    """install_command returns 1 when protocol YAML is invalid."""
    monkeypatch.setattr("snodo.infrastructure.paths.require_project_root", lambda: str(temp_project))
    bad_proto = temp_project / ".snodo" / "bad.yml"
    bad_proto.write_text("invalid: [yaml: :")

    args = SimpleNamespace(protocol=".snodo/bad.yml")
    res = install_command(args)

    assert res == 1
    err = capsys.readouterr().err
    assert "Failed to load protocol" in err


def test_install_command_install_exception(temp_project, monkeypatch, capsys):
    """install_command returns 1 when install() raises an exception."""
    monkeypatch.setattr("snodo.infrastructure.paths.require_project_root", lambda: str(temp_project))
    monkeypatch.setattr(
        "snodo.cli.commands.install_cmd.install",
        MagicMock(side_effect=PermissionError("Permission denied")),
    )
    monkeypatch.setattr("snodo.cli.commands.install_cmd.get_claude_config_path", lambda: temp_project / "claude.json")

    args = SimpleNamespace(protocol=".snodo/protocol.yml")
    res = install_command(args)

    assert res == 1
    err = capsys.readouterr().err
    assert "Failed to install MCP entries" in err


# ============================================================================
# 2. uninstall_command tests
# ============================================================================

def test_uninstall_command_happy_path(temp_project, monkeypatch, capsys):
    """uninstall_command removes project MCP entries and returns 0."""
    monkeypatch.setattr("snodo.infrastructure.paths.require_project_root", lambda: str(temp_project))
    mock_uninstall = MagicMock(return_value=["snodo-dev"])
    monkeypatch.setattr("snodo.cli.commands.install_cmd.uninstall", mock_uninstall)
    monkeypatch.setattr("snodo.cli.commands.install_cmd.get_claude_config_path", lambda: temp_project / "claude.json")
    monkeypatch.setattr("snodo.cli.commands.install_cmd._audit_global", MagicMock())

    args = SimpleNamespace(mode=None, all_entries=False, purge=False, orphans=False, yes=False, protocol=".snodo/protocol.yml")
    res = uninstall_command(args)

    assert res == 0
    assert mock_uninstall.called


def test_uninstall_command_protocol_missing(temp_project, monkeypatch, capsys):
    """uninstall_command returns 1 when project protocol is missing."""
    monkeypatch.setattr("snodo.infrastructure.paths.require_project_root", lambda: str(temp_project))

    args = SimpleNamespace(mode=None, all_entries=False, purge=False, orphans=False, yes=False, protocol=".snodo/missing.yml")
    res = uninstall_command(args)

    assert res == 1
    err = capsys.readouterr().err
    assert "Protocol file not found" in err


def test_uninstall_command_load_failure(temp_project, monkeypatch, capsys):
    """uninstall_command returns 1 when protocol loading fails."""
    monkeypatch.setattr("snodo.infrastructure.paths.require_project_root", lambda: str(temp_project))
    bad_proto = temp_project / ".snodo" / "bad.yml"
    bad_proto.write_text("invalid: [yaml: :")

    args = SimpleNamespace(mode=None, all_entries=False, purge=False, orphans=False, yes=False, protocol=".snodo/bad.yml")
    res = uninstall_command(args)

    assert res == 1
    err = capsys.readouterr().err
    assert "Failed to load protocol" in err


def test_uninstall_command_exception(temp_project, monkeypatch, capsys):
    """uninstall_command returns 1 when uninstall() raises an exception."""
    monkeypatch.setattr("snodo.infrastructure.paths.require_project_root", lambda: str(temp_project))
    monkeypatch.setattr(
        "snodo.cli.commands.install_cmd.uninstall",
        MagicMock(side_effect=RuntimeError("Uninstall error")),
    )
    monkeypatch.setattr("snodo.cli.commands.install_cmd.get_claude_config_path", lambda: temp_project / "claude.json")

    args = SimpleNamespace(mode=None, all_entries=False, purge=False, orphans=False, yes=False, protocol=".snodo/protocol.yml")
    res = uninstall_command(args)

    assert res == 1
    err = capsys.readouterr().err
    assert "Failed to uninstall" in err


def test_uninstall_all_entries_path(temp_project, monkeypatch):
    """_uninstall_all_entries removes all snodo entries."""
    monkeypatch.setattr("snodo.cli.commands.install_cmd.get_claude_config_path", lambda: temp_project / "claude.json")
    monkeypatch.setattr("snodo.cli.commands.install_cmd.uninstall_all", MagicMock(return_value=["snodo-a", "snodo-b"]))
    monkeypatch.setattr("snodo.cli.commands.install_cmd._audit_global", MagicMock())

    args = SimpleNamespace(all_entries=True, mode=None, purge=False, orphans=False, yes=False)
    res = uninstall_command(args)
    assert res == 0


def test_uninstall_all_entries_failure(temp_project, monkeypatch, capsys):
    """_uninstall_all_entries returns 1 on exception."""
    monkeypatch.setattr("snodo.cli.commands.install_cmd.get_claude_config_path", lambda: temp_project / "claude.json")
    monkeypatch.setattr("snodo.cli.commands.install_cmd.uninstall_all", MagicMock(side_effect=RuntimeError("Config unreadable")))

    res = _uninstall_all_entries()
    assert res == 1
    err = capsys.readouterr().err
    assert "Config unreadable" in err


# ============================================================================
# 3. Orphan uninstall tests
# ============================================================================

def test_uninstall_orphans_happy_path(temp_project, monkeypatch, capsys):
    """_uninstall_orphans detects and removes orphan entries when confirmed."""
    monkeypatch.setattr("snodo.cli.commands.install_cmd.get_claude_config_path", lambda: temp_project / "claude.json")
    orphans_list = [{"entry_name": "snodo-old", "missing_path": "/missing/path"}]
    monkeypatch.setattr("snodo.cli.commands.install_cmd.scan_orphans", lambda config_path: orphans_list)
    monkeypatch.setattr("snodo.cli.commands.install_cmd.remove_orphans", lambda config_path: ["snodo-old"])
    monkeypatch.setattr("snodo.cli.commands.install_cmd._audit_global", MagicMock())

    res = _uninstall_orphans(skip_prompt=True)

    assert res == 0
    out = capsys.readouterr().out
    assert "Found 1 orphan MCP entry(ies)" in out
    assert "Removed 1 orphan(s)" in out


def test_uninstall_orphans_none_found(temp_project, monkeypatch, capsys):
    """_uninstall_orphans returns 0 when no orphans are found."""
    monkeypatch.setattr("snodo.cli.commands.install_cmd.get_claude_config_path", lambda: temp_project / "claude.json")
    monkeypatch.setattr("snodo.cli.commands.install_cmd.scan_orphans", lambda config_path: [])

    res = _uninstall_orphans(skip_prompt=True)

    assert res == 0
    assert "No orphan MCP entries found." in capsys.readouterr().out


def test_uninstall_orphans_prompt_aborted(temp_project, monkeypatch, capsys):
    """_uninstall_orphans returns 0 when user declines prompt."""
    monkeypatch.setattr("snodo.cli.commands.install_cmd.get_claude_config_path", lambda: temp_project / "claude.json")
    orphans_list = [{"entry_name": "snodo-old", "missing_path": "/missing/path"}]
    monkeypatch.setattr("snodo.cli.commands.install_cmd.scan_orphans", lambda config_path: orphans_list)
    monkeypatch.setattr("builtins.input", lambda _: "n")

    res = _uninstall_orphans(skip_prompt=False)

    assert res == 0
    assert "Aborted." in capsys.readouterr().out


def test_uninstall_orphans_scan_error(temp_project, monkeypatch, capsys):
    """_uninstall_orphans returns 1 when scanning raises exception."""
    monkeypatch.setattr("snodo.cli.commands.install_cmd.get_claude_config_path", lambda: temp_project / "claude.json")
    monkeypatch.setattr("snodo.cli.commands.install_cmd.scan_orphans", MagicMock(side_effect=RuntimeError("Scan error")))

    res = _uninstall_orphans(skip_prompt=True)

    assert res == 1
    assert "Error scanning for orphans" in capsys.readouterr().err


# ============================================================================
# 4. Purge uninstall tests
# ============================================================================

def test_uninstall_purge_happy_path(temp_project, monkeypatch, capsys):
    """_uninstall_purge removes entries and project state."""
    monkeypatch.setattr("snodo.infrastructure.paths.require_project_root", lambda: str(temp_project))
    monkeypatch.setattr("snodo.cli.commands.install_cmd.uninstall_all", MagicMock(return_value=["snodo-dev"]))
    monkeypatch.setattr(
        "snodo.cli.commands.install_cmd.purge_project_state",
        lambda root: {"purged_paths": [".snodo"], "session_count": 1},
    )
    monkeypatch.setattr("snodo.cli.commands.install_cmd._audit_global", MagicMock())

    res = _uninstall_purge(temp_project / "claude.json", mode_filter=None, skip_prompt=True)

    assert res == 0
    out = capsys.readouterr().out
    assert "Purge complete." in out


def test_uninstall_purge_aborted(temp_project, monkeypatch, capsys):
    """_uninstall_purge returns 1 when prompt declined."""
    monkeypatch.setattr("snodo.infrastructure.paths.require_project_root", lambda: str(temp_project))
    monkeypatch.setattr("builtins.input", lambda _: "n")

    res = _uninstall_purge(temp_project / "claude.json", mode_filter=None, skip_prompt=False)

    assert res == 1
    assert "Aborted." in capsys.readouterr().out


# ============================================================================
# 5. Audit & Typer registration tests
# ============================================================================

def test_audit_global_warning_on_failure(monkeypatch):
    """_audit_global logs warning if audit appending fails."""
    mock_audit_cls = MagicMock()
    mock_audit_instance = MagicMock()
    mock_audit_instance.append_event.side_effect = RuntimeError("Disk error")
    mock_audit_cls.return_value = mock_audit_instance
    monkeypatch.setattr("snodo.infrastructure.audit.AuditLog", mock_audit_cls)

    _audit_global("test_event", {"data": "x"})
    assert mock_audit_instance.append_event.called


def test_cli_register_commands(temp_project, monkeypatch):
    """Typer app registration exposes install and uninstall commands."""
    app = typer.Typer()
    register(app)

    runner = CliRunner()
    monkeypatch.setattr("snodo.infrastructure.paths.require_project_root", lambda: str(temp_project))
    monkeypatch.setattr("snodo.cli.commands.install_cmd.install", MagicMock(return_value=([], [])))

    res = runner.invoke(app, ["install", "--protocol", ".snodo/protocol.yml"])
    assert res.exit_code == 0

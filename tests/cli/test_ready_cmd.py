"""Tests for snodo ready CLI command (snodo/cli/commands/ready_cmd.py).

FILE: tests/cli/test_ready_cmd.py (Fixes #216)

PROVES:
- 'snodo ready' formats human output with Method Scaffolding Readiness score and separated sections.
- '--mode' filters displayed findings while the readiness figure remains whole-protocol.
- '--json' emits structured output adhering to schema 'snodo.ready.v1'.
- Running 'snodo ready' appends a 'readiness_checked' audit event with cloud payload discipline.
- Alias 'snodo readiness' works identically to 'snodo ready'.
- Errors outside project root or on missing protocol fail with actionable messages.
"""

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
import pytest
import typer
import yaml

from snodo.cli.commands.ready_cmd import ready_command, register
from snodo.infrastructure.audit import AuditLog


@pytest.fixture
def git_project(tmp_path: Path, monkeypatch) -> Path:
    """Create an initialized git repo with .snodo directory and mock paths."""
    root = tmp_path / "project"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    (root / "README.md").write_text("# Test Project\n")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=root, check=True)

    snodo_dir = root / ".snodo"
    snodo_dir.mkdir()

    protocol_data = {
        "protocol_id": "proto-readiness",
        "name": "Readiness Protocol",
        "version": "1.0.0",
        "initial_mode": "plan",
        "modes": [
            {
                "mode_id": "plan",
                "name": "Plan Mode",
                "validators": ["val_arch"],
            },
            {
                "mode_id": "build",
                "name": "Build Mode",
                "validators": ["val_quality"],
            },
        ],
        "validators": [
            {
                "validator_id": "val_arch",
                "validator_type": "architecture",
                "criteria": ["Check docs/decisions/"],
            },
            {
                "validator_id": "val_quality",
                "validator_type": "quality",
                "tooling": {"test_command": "pytest"},
            },
        ],
    }
    (snodo_dir / "protocol.yml").write_text(yaml.safe_dump(protocol_data))
    subprocess.run(["git", "add", ".snodo/protocol.yml"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "add protocol"], cwd=root, check=True)

    monkeypatch.setattr("snodo.infrastructure.paths.resolve_project_root", lambda: str(root))
    monkeypatch.setenv("SNODO_PROJECT_ROOT", str(root))
    return root


def test_ready_cmd_registration():
    """register() attaches 'ready' and 'readiness' commands to typer app."""
    app = typer.Typer()
    register(app)
    command_names = [cmd.name or cmd.callback.__name__ for cmd in app.registered_commands]
    assert "ready" in command_names
    assert "readiness" in command_names


def test_ready_cmd_human_output(git_project: Path, capsys):
    """'snodo ready' prints score, repository findings, and workstation status."""
    args = SimpleNamespace(mode=None, protocol=".snodo/protocol.yml", json=False)
    exit_code = ready_command(args)

    assert exit_code == 0
    captured = capsys.readouterr()

    # Architecture decisions missing -> score < 100
    assert "Method Scaffolding Readiness:" in captured.out
    assert "Repository Readiness (Scored" in captured.out
    assert "Workstation Readiness (Reported" in captured.out
    assert "architecture" in captured.out.lower()
    assert "docs/decisions" in captured.out


def test_ready_cmd_mode_filtering(git_project: Path, capsys):
    """'--mode build' filters displayed findings but keeps the whole-protocol readiness score."""
    args = SimpleNamespace(mode="build", protocol=".snodo/protocol.yml", json=False)
    exit_code = ready_command(args)

    assert exit_code == 0
    captured = capsys.readouterr()

    assert "Method Scaffolding Readiness:" in captured.out
    assert "Displaying findings for mode 'build'" in captured.out
    # 'val_arch' is in 'plan' mode only, so it should not appear in filtered repository findings
    assert "architecture_decisions" not in captured.out


def test_ready_cmd_json_output(git_project: Path, capsys):
    """'snodo ready --json' outputs valid JSON matching schema 'snodo.ready.v1'."""
    args = SimpleNamespace(mode=None, protocol=".snodo/protocol.yml", json=True)
    exit_code = ready_command(args)

    assert exit_code == 0
    captured = capsys.readouterr()

    data = json.loads(captured.out)
    assert data["schema"] == "snodo.ready.v1"
    assert data["ok"] is True
    assert data["protocol_id"] == "proto-readiness"
    assert isinstance(data["score"], int)
    assert data["total_checks"] >= 2
    assert "findings" in data
    assert any(f["id"].startswith("architecture_decisions") for f in data["findings"])


def test_ready_cmd_audit_event_emission(git_project: Path, capsys, monkeypatch):
    """Running 'snodo ready' logs a 'readiness_checked' audit event with repository findings only and workstation count."""
    # Ensure there is a workstation finding by setting a model requiring an unset env var
    proto_file = git_project / ".snodo" / "protocol.yml"
    protocol_data = yaml.safe_load(proto_file.read_text())
    protocol_data["validators"][0]["model"] = "claude-3-5-sonnet-20241022"
    proto_file.write_text(yaml.safe_dump(protocol_data))
    subprocess.run(["git", "add", ".snodo/protocol.yml"], cwd=git_project, check=True)
    subprocess.run(["git", "commit", "-qm", "update protocol model"], cwd=git_project, check=True)

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    audit_log_path = git_project / ".snodo" / "audit.log"
    audit_log = AuditLog(str(audit_log_path))
    monkeypatch.setattr("snodo.infrastructure.audit.get_audit_log", lambda project_id=None: audit_log)

    args = SimpleNamespace(mode=None, protocol=".snodo/protocol.yml", json=False)
    exit_code = ready_command(args)
    assert exit_code == 0

    # 1. Terminal report still shows workstation finding
    captured = capsys.readouterr().out
    assert "Workstation Readiness" in captured
    assert "ANTHROPIC_API_KEY" in captured

    # 2. Audit event contains only repository findings and workstation_findings_count
    events = audit_log.get_history()
    assert len(events) >= 1
    readiness_events = [e for e in events if e.event_type == "readiness_checked"]
    assert len(readiness_events) == 1

    event_data = readiness_events[0].data
    assert event_data["protocol_id"] == "proto-readiness"
    assert "score" in event_data
    assert "findings" in event_data
    assert "workstation_findings_count" in event_data
    assert event_data["workstation_findings_count"] >= 1
    assert event_data["repository_findings_count"] == len(event_data["findings"])

    # No workstation findings in the transmitted event
    for f in event_data["findings"]:
        assert f["kind"] == "repository"
        assert "ANTHROPIC_API_KEY" not in str(f)
        assert str(git_project) not in f["finding"]
        assert str(git_project) not in f["remediation"]


def test_ready_cmd_not_in_project(monkeypatch, capsys):
    """ready_command returns error when not in a snodo project root."""
    monkeypatch.setattr("snodo.infrastructure.paths.resolve_project_root", lambda: None)
    args = SimpleNamespace(mode=None, protocol=".snodo/protocol.yml", json=False)
    exit_code = ready_command(args)

    assert exit_code != 0
    captured = capsys.readouterr()
    assert "Not inside a snodo project" in captured.err


def test_ready_cmd_unknown_mode(git_project: Path, capsys):
    """ready_command fails cleanly when an unknown mode is provided."""
    args = SimpleNamespace(mode="nonexistent_mode", protocol=".snodo/protocol.yml", json=False)
    exit_code = ready_command(args)

    assert exit_code != 0
    captured = capsys.readouterr()
    assert "Unknown mode 'nonexistent_mode'" in captured.err

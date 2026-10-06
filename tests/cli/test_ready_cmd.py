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
    assert data["schema"] == "snodo.ready.v2"
    assert data["ok"] is True
    assert data["protocol_id"] == "proto-readiness"
    assert isinstance(data["score"], int)
    assert data["total_checks"] >= 2
    assert "findings" in data
    assert any(f["id"].startswith("architecture_decisions") for f in data["findings"])
    assert set(data["extensions"]) == {
        "snodo.providers", "snodo.validators", "snodo.predicates", "snodo.coders",
    }


@pytest.mark.parametrize("load_error, expected_status", [(None, "installed"), (ImportError("missing dependency"), "failed")])
def test_ready_reports_validator_extension_status(git_project: Path, capsys, monkeypatch, load_error, expected_status):
    from snodo.validators.context import ValidatorBase
    from snodo.validators.registry import _default_registry

    class FakeValidator(ValidatorBase):
        @classmethod
        def registered_type(cls):
            return "ready_fake"

        def evaluate(self, context):
            raise NotImplementedError

    class FakeEntryPoint:
        name = "ready_fake"

        def load(self):
            if load_error:
                raise load_error
            return FakeValidator

    monkeypatch.setattr("importlib.metadata.entry_points", lambda **kwargs: [FakeEntryPoint()])
    _default_registry.discover_plugins()

    code = ready_command(SimpleNamespace(mode=None, protocol=".snodo/protocol.yml", json=True))
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    plugin = data["extensions"]["snodo.validators"]["ready_fake"]
    assert plugin["status"] == expected_status
    if load_error:
        assert "ImportError: missing dependency" == plugin["error"]


def test_ready_reports_unknown_and_legacy_capability_grants(git_project: Path, capsys):
    proto_file = git_project / ".snodo" / "protocol.yml"
    protocol_data = yaml.safe_load(proto_file.read_text())
    protocol_data["modes"][0]["tools"] = ["plan", "resolve", "plna"]
    proto_file.write_text(yaml.safe_dump(protocol_data))

    exit_code = ready_command(SimpleNamespace(mode=None, protocol=".snodo/protocol.yml", json=True))
    assert exit_code == 0
    result = json.loads(capsys.readouterr().out)
    assert len(result["warnings"]) == 2
    assert any("Mode 'plan'" in warning and "'resolve'" in warning for warning in result["warnings"])
    assert any("'plna'" in warning and "Known capabilities:" in warning for warning in result["warnings"])


def test_mcp_ready_reports_same_capability_warning(git_project: Path, monkeypatch):
    from snodo.mcp.diagnostic_handlers import DiagnosticToolHandler

    proto_file = git_project / ".snodo" / "protocol.yml"
    protocol_data = yaml.safe_load(proto_file.read_text())
    protocol_data["modes"][0]["tools"] = ["resolve"]
    proto_file.write_text(yaml.safe_dump(protocol_data))
    monkeypatch.setattr("snodo.infrastructure.paths.resolve_project_root", lambda: str(git_project))

    result = DiagnosticToolHandler(str(git_project)).ready({})
    assert result["ok"] is True
    assert len(result["warnings"]) == 1
    assert "Mode 'plan'" in result["warnings"][0]
    assert "unknown capability 'resolve'" in result["warnings"][0]


def test_mcp_ready_uses_the_served_project_not_the_process_cwd(git_project: Path, tmp_path, monkeypatch):
    """Claude Desktop starts MCP servers outside the project; ready must still find it."""
    from snodo.mcp.diagnostic_handlers import DiagnosticToolHandler

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.delenv("SNODO_PROJECT_ROOT", raising=False)

    result = DiagnosticToolHandler(str(git_project)).ready({})

    assert result["ok"] is True, result
    assert Path(result["project_root"]).resolve() == git_project.resolve()


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


def test_ready_reports_mcp_install_dependency_drift(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from snodo.cli.commands.ready_cmd import _append_mcp_install_findings
    from snodo.mcp.installer import ClientTarget

    launcher = tmp_path / "python"
    launcher.touch()
    launcher.chmod(0o755)
    config = tmp_path / "claude.json"
    config.write_text(json.dumps({"mcpServers": {
        "snodo-demo-build": {"command": str(launcher), "args": ["-m", "snodo", "serve"]},
    }}))
    target = ClientTarget("Claude Desktop", config, "mcpServers", "json")
    monkeypatch.setattr("snodo.mcp.installer.known_client_targets", lambda: [target])
    monkeypatch.setattr("snodo.cli.commands.ready_cmd.subprocess.run", lambda *a, **k: SimpleNamespace(returncode=1))
    assessment = SimpleNamespace(workstation_findings=[])

    _append_mcp_install_findings(assessment)

    assert len(assessment.workstation_findings) == 1
    finding = assessment.workstation_findings[0]
    assert "dependencies" in finding.description
    assert "Claude Desktop" in finding.description
    assert str(config) in finding.description
    assert "snodo serve --mcp-install" in finding.remediation


def test_ready_skips_remote_mcp_entries_and_distinguishes_clients(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from snodo.cli.commands.ready_cmd import _append_mcp_install_findings
    from snodo.mcp.installer import ClientTarget

    local = tmp_path / "python"
    local.touch()
    configs = [
        ClientTarget("Claude Desktop", tmp_path / "claude.json", "mcpServers", "json"),
        ClientTarget("Codex", tmp_path / "codex.json", "mcp_servers", "json"),
    ]
    configs[0].config_path.write_text(json.dumps({"mcpServers": {
        "snodo-remote": {"command": "ssh", "args": ["host", "snodo"]},
        "snodo-local": {"command": str(local), "args": ["-m", "snodo"]},
    }}))
    configs[1].config_path.write_text(json.dumps({"mcp_servers": {
        "snodo-local": {"command": str(local), "args": ["-m", "snodo"]},
    }}))
    monkeypatch.setattr("snodo.mcp.installer.known_client_targets", lambda: configs)
    monkeypatch.setattr("snodo.cli.commands.ready_cmd.subprocess.run", lambda *a, **k: SimpleNamespace(returncode=1))
    assessment = SimpleNamespace(workstation_findings=[])

    _append_mcp_install_findings(assessment)

    assert len(assessment.workstation_findings) == 2
    descriptions = [f.description for f in assessment.workstation_findings]
    assert all("snodo-local" in d for d in descriptions)
    assert all("snodo-remote" not in d for d in descriptions)
    assert "Claude Desktop" in descriptions[0] and "claude.json" in descriptions[0]
    assert "Codex" in descriptions[1] and "codex.json" in descriptions[1]

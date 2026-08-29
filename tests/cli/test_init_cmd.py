"""Behavioral tests for snodo init CLI command (snodo/cli/commands/init_cmd.py).

FILE: tests/cli/test_init_cmd.py
"""

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
import pytest
import typer
import yaml

from snodo.cli.commands import PROTOCOL_TEMPLATES, list_templates
from snodo.cli.commands.init_cmd import (
    _configure_test_command,
    _detect_test_command,
    _select_template,
    init_command,
    register,
)


@pytest.fixture
def git_project_dir(tmp_path):
    """Fixture providing a git-initialized temporary directory."""
    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(tmp_path), capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(tmp_path), capture_output=True, check=True)
    readme = tmp_path / "README.md"
    readme.write_text("test project")
    subprocess.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=str(tmp_path), capture_output=True, check=True)
    return tmp_path


# ============================================================================
# 1. Command Registration
# ============================================================================

def test_init_register():
    """register() registers top-level init command on typer App."""
    app = typer.Typer()
    register(app)
    command_names = [cmd.name or cmd.callback.__name__ for cmd in app.registered_commands]
    assert "init" in command_names


# ============================================================================
# 2. Defect Fix: _select_template Prompt Abort & Non-Interactive Behavior
# ============================================================================

def test_select_template_keyboard_interrupt_aborts(capsys, monkeypatch):
    """KeyboardInterrupt (Ctrl-C) at template prompt aborts without selecting team default."""
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": (_ for _ in ()).throw(KeyboardInterrupt))

    args = SimpleNamespace(template=None)
    with pytest.raises(SystemExit) as exc:
        _select_template(args)

    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "Aborted: initialization cancelled." in err


def test_select_template_eof_in_non_interactive_context(capsys, monkeypatch):
    """EOF in non-interactive context refuses silently choosing a default template."""
    monkeypatch.setattr("builtins.input", lambda prompt="": (_ for _ in ()).throw(EOFError))

    args = SimpleNamespace(template=None)
    with pytest.raises(SystemExit) as exc:
        _select_template(args)

    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "--template option is required in a non-interactive context" in err
    assert "Available templates:" in err
    for template_name in list_templates():
        assert template_name in err


# ============================================================================
# 3. Shipped Templates & Unknown Template Flags
# ============================================================================

@pytest.mark.parametrize("template_name", list_templates())
def test_init_command_with_all_shipped_templates(template_name, git_project_dir, monkeypatch, capsys):
    """init_command succeeds for every shipped protocol template."""
    monkeypatch.chdir(git_project_dir)

    args = SimpleNamespace(
        template=template_name,
        force=False,
        mode=None,
        project_id=None,
        force_keygen=False,
        yes=True,
        no_input=False,
        test_command=None,
    )

    res = init_command(args)
    assert res == 0

    snodo_dir = git_project_dir / ".snodo"
    assert snodo_dir.is_dir()
    protocol_file = snodo_dir / "protocol.yml"
    assert protocol_file.exists()

    data = yaml.safe_load(protocol_file.read_text())
    assert isinstance(data, dict)

    out = capsys.readouterr().out
    assert "Snodo initialized successfully!" in out
    assert "Configure provider credentials" in out


def test_init_command_unknown_template(git_project_dir, monkeypatch, capsys):
    """init_command with an unknown --template exits non-zero and lists available templates."""
    monkeypatch.chdir(git_project_dir)

    args = SimpleNamespace(
        template="nonexistent_template_xyz",
        force=False,
        mode=None,
        project_id=None,
        force_keygen=False,
        yes=True,
        no_input=False,
        test_command=None,
    )

    with pytest.raises(SystemExit) as exc:
        init_command(args)

    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "Error: Unknown template 'nonexistent_template_xyz'." in err
    assert "Available templates:" in err
    assert not (git_project_dir / ".snodo").exists()


# ============================================================================
# 4. Overwrite Protection (--force)
# ============================================================================

def test_init_command_existing_snodo_dir_without_force(git_project_dir, monkeypatch, capsys):
    """init_command fails when .snodo/ already exists without --force."""
    monkeypatch.chdir(git_project_dir)
    (git_project_dir / ".snodo").mkdir()

    args = SimpleNamespace(
        template="solo",
        force=False,
        yes=True,
    )

    res = init_command(args)
    assert res == 1
    err = capsys.readouterr().err
    assert "Error: .snodo/ already exists. Use --force to overwrite." in err


def test_init_command_existing_snodo_dir_with_force(git_project_dir, monkeypatch, capsys):
    """init_command succeeds over existing .snodo/ directory when --force is supplied."""
    monkeypatch.chdir(git_project_dir)
    (git_project_dir / ".snodo").mkdir()

    args = SimpleNamespace(
        template="solo",
        force=True,
        mode=None,
        project_id=None,
        force_keygen=False,
        yes=True,
        no_input=False,
        test_command=None,
    )

    res = init_command(args)
    assert res == 0
    out = capsys.readouterr().out
    assert "Warning: Overwriting existing .snodo/ directory" in out
    assert "Snodo initialized successfully!" in out


# ============================================================================
# 5. Marker File Test Command Auto-Detection
# ============================================================================

@pytest.mark.parametrize(
    "marker_file,expected_cmd",
    [
        ("package.json", "npm test"),
        ("pyproject.toml", "pytest"),
        ("setup.py", "pytest"),
        ("setup.cfg", "pytest"),
        ("Cargo.toml", "cargo test"),
        ("Makefile", "make test"),
        ("go.mod", "go test ./..."),
    ],
)
def test_detect_test_command_rules(marker_file, expected_cmd, tmp_path):
    """_detect_test_command resolves correct command for each project marker file."""
    (tmp_path / marker_file).write_text("marker content")
    detected = _detect_test_command(tmp_path)
    assert detected == expected_cmd


def test_detect_test_command_none(tmp_path):
    """_detect_test_command returns None when no marker files exist."""
    assert _detect_test_command(tmp_path) is None


def test_configure_test_command_injection(tmp_path):
    """_configure_test_command injects detected test command into quality validator."""
    (tmp_path / "package.json").write_text("{}")
    template_raw = PROTOCOL_TEMPLATES["solo"]

    args = SimpleNamespace(test_command=None, yes=True)
    configured = _configure_test_command(args, template_raw, tmp_path)

    data = yaml.safe_load(configured)
    quality_v = next(v for v in data["validators"] if v.get("validator_id") == "quality")
    assert quality_v["tooling"]["test_command"] == "npm test"


# ============================================================================
# 6. RS256 Keypair Generation
# ============================================================================

def test_init_command_generates_signing_keys(git_project_dir, monkeypatch, capsys):
    """init_command generates RS256 decision signing keypair."""
    monkeypatch.chdir(git_project_dir)

    args = SimpleNamespace(
        template="solo",
        force=False,
        mode=None,
        project_id=None,
        force_keygen=True,
        yes=True,
        no_input=False,
        test_command=None,
    )

    res = init_command(args)
    assert res == 0

    out = capsys.readouterr().out
    assert ("RS256 keypair generated:" in out) or ("Using existing RS256 keypair:" in out)
    assert "Private:" in out
    assert "Public:" in out


# ============================================================================
# 7. Non-Project & Home Directory Failure Paths
# ============================================================================

def test_init_command_refuses_home_directory(monkeypatch, capsys):
    """init_command refuses to initialize at user home directory."""
    monkeypatch.setattr(Path, "cwd", lambda: Path.home())

    args = SimpleNamespace(template="solo", yes=True)
    res = init_command(args)
    assert res == 1
    err = capsys.readouterr().err
    assert "Error: Cannot initialise a Snodo project at your home directory." in err


def test_init_command_refuses_non_git_directory(tmp_path, monkeypatch, capsys):
    """init_command refuses to initialize when not in a git repository."""
    monkeypatch.chdir(tmp_path)

    args = SimpleNamespace(template="solo", yes=True)
    res = init_command(args)
    assert res == 1
    err = capsys.readouterr().err
    assert "Error: snodo requires a git repository. Run 'git init' first." in err

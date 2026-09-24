"""Remote host selection and SSH preflight tests (ADR 055)."""

import os
import subprocess
from pathlib import Path

from snodo.cli.main import main
from snodo.remote_host import check_remote_host, select_execution_host
from snodo.version import __version__


def test_host_precedence_and_local_default(monkeypatch):
    monkeypatch.delenv("SNODO_HOST", raising=False)
    assert select_execution_host({}) is None
    assert select_execution_host({"host": "configured"}) == "configured"
    monkeypatch.setenv("SNODO_HOST", "environment")
    assert select_execution_host({"host": "configured"}) == "environment"
    assert select_execution_host({"host": "configured"}, {"SNODO_HOST": "override"}) == "override"


def _fake_ssh(tmp_path: Path, monkeypatch, mode: str = ""):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    executable = bindir / "ssh"
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import os, sys\n"
        "command = sys.argv[-1]\n"
        "mode = os.environ.get('FAKE_SSH_MODE', '')\n"
        "if command == 'true':\n"
        "    sys.exit(1 if mode == 'reachable' else 0)\n"
        "elif command == 'snodo --version':\n"
        "    print('snodo wrong' if mode == 'version' else 'snodo " + __version__ + "')\n"
        "    sys.exit(1 if mode == 'version-command' else 0)\n"
        "else:\n"
        "    print('true')\n"
        "    print('git@github.com:org/other.git' if mode == 'project_clone' else 'git@github.com:org/project.git')\n"
        "    sys.exit(1 if mode == 'project_clone' else 0)\n"
    )
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_SSH_MODE", mode)


def _local_repo(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "remote", "add", "origin", "https://github.com/org/project.git"], check=True)
    return root


def test_preflight_passes_with_fake_ssh(tmp_path, monkeypatch):
    _fake_ssh(tmp_path, monkeypatch)
    checks = check_remote_host("worker", _local_repo(tmp_path), "/remote/project")
    assert [check.name for check in checks] == ["reachable", "version", "project_clone"]
    assert all(check.ok for check in checks)


def test_each_preflight_failure_is_named_and_has_command(tmp_path, monkeypatch):
    root = _local_repo(tmp_path)
    for name in ("reachable", "version", "project_clone"):
        _fake_ssh(tmp_path, monkeypatch, name)
        checks = check_remote_host("worker", root, "/remote/project")
        failed = [check for check in checks if not check.ok]
        assert [check.name for check in failed] == [name]
        assert failed[0].command


def test_host_check_json_reports_no_remote_configured(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    result = main(["host", "check", "--json"])
    assert result == 0
    assert '"host": null' in capsys.readouterr().out


def test_config_protocol_accepts_remote_fields():
    from snodo.compiler.models import ExecutionConfig

    configured = ExecutionConfig(host="worker", host_path="/srv/project")
    assert configured.host == "worker"
    assert configured.host_path == "/srv/project"

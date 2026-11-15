from pathlib import Path
from types import SimpleNamespace

import typer
from typer.testing import CliRunner

from snodo.cli.commands import dashboard_cmd


runner = CliRunner()


def make_app():
    app = typer.Typer()
    dashboard_cmd.register(app)
    return app


def test_no_flags_and_cli_use_terminal_path(monkeypatch):
    calls = []
    monkeypatch.setattr(dashboard_cmd, "dashboard_command", lambda args: calls.append(args) or 0)
    app = make_app()

    assert runner.invoke(app, []).exit_code == 0
    assert runner.invoke(app, ["--cli"]).exit_code == 0
    assert calls == [SimpleNamespace(), SimpleNamespace()]


def test_web_root_saves_and_serves_projects(monkeypatch, tmp_path):
    project = object()
    called = {}
    monkeypatch.setattr("snodo.web.roots.save_roots", lambda roots: called.update(saved=roots))
    monkeypatch.setattr("snodo.web.projects.resolve_projects", lambda roots, cwd: [project])
    monkeypatch.setattr("snodo.web.app.serve", lambda projects, port: called.update(served=projects, port=port))

    result = runner.invoke(make_app(), ["--web", "--root", str(tmp_path), "--port", "8123"])
    assert result.exit_code == 0, result.output
    assert called["saved"] == [Path(tmp_path)]
    assert called["served"] == [project]
    assert called["port"] == 8123


def test_web_uses_saved_roots(monkeypatch):
    called = {}
    monkeypatch.setattr("snodo.web.roots.load_roots", lambda: ["/saved"])
    monkeypatch.setattr("snodo.web.projects.resolve_projects", lambda roots, cwd: called.update(roots=roots) or [object()])
    monkeypatch.setattr("snodo.web.app.serve", lambda projects, port: None)

    result = runner.invoke(make_app(), ["--web"])
    assert result.exit_code == 0, result.output
    assert called["roots"] == ["/saved"]


def test_web_without_projects_fails_with_guidance(monkeypatch):
    monkeypatch.setattr("snodo.web.roots.load_roots", lambda: [])
    monkeypatch.setattr("snodo.web.projects.resolve_projects", lambda roots, cwd: [])
    result = runner.invoke(make_app(), ["--web"])
    assert result.exit_code != 0
    assert "pass --root or run this command inside a snodo project" in result.output


def test_cli_rejects_web_options(monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard_cmd, "dashboard_command", lambda args: 0)
    result = runner.invoke(make_app(), ["--cli", "--root", str(tmp_path)])
    assert result.exit_code != 0
    assert "only valid with --web" in result.output


def test_help_shows_mode_options():
    result = runner.invoke(make_app(), ["--help"])
    assert result.exit_code == 0
    assert "--web" in result.output
    assert "--cli" in result.output


def test_help_describes_os_chosen_port_default():
    result = runner.invoke(make_app(), ["--help"])
    assert result.exit_code == 0
    assert "OS-chosen free port" in result.output

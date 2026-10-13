"""Tests for the side-effect-free home view renderer."""

from types import SimpleNamespace

from snodo.cli import home_view


def test_outside_git_suggests_entering_repository(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    text = home_view.render_home_view()
    assert "cd into a git repository" in text
    assert "\x1b[" not in text


def test_git_without_snodo_suggests_init(tmp_path, monkeypatch):
    from git import Repo

    Repo.init(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert "snodo init" in home_view.render_home_view()


def test_ready_project_and_blocking_finding_choose_next_step(tmp_path, monkeypatch):
    root = tmp_path / "my-app"
    root.mkdir()
    (root / ".snodo").mkdir()
    monkeypatch.setattr(home_view, "resolve_project_root", lambda: str(root), raising=False)
    # Replace collaborators to keep this renderer test independent of project fixtures.
    from snodo.infrastructure import paths
    monkeypatch.setattr(paths, "resolve_project_root", lambda: str(root))
    protocol = SimpleNamespace(initial_mode="producer", modes=[SimpleNamespace(mode_id="producer", coder="opencode-cli")])
    monkeypatch.setattr("snodo.cli.commands.load_protocol", lambda _path: protocol)
    monkeypatch.setattr("snodo.readiness.checker.assess_readiness", lambda *_a, **_k: SimpleNamespace(all_findings=[]))
    monkeypatch.setattr("snodo.cli.commands.cloud_status.sync_issue_notice", lambda: None)
    text = home_view.render_home_view()
    assert "my-app · producer" in text
    assert 'snodo run "describe the change you want"' in text

    finding = SimpleNamespace(severity=SimpleNamespace(value="blocker"), description="missing requirement")
    monkeypatch.setattr("snodo.readiness.checker.assess_readiness", lambda *_a, **_k: SimpleNamespace(all_findings=[finding]))
    assert "snodo ready" in home_view.render_home_view()


def test_cloud_sync_notice_is_rendered_as_attention(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    (root / ".snodo").mkdir()
    from snodo.infrastructure import paths
    monkeypatch.setattr(paths, "resolve_project_root", lambda: str(root))
    protocol = SimpleNamespace(initial_mode="producer", modes=[])
    monkeypatch.setattr("snodo.cli.commands.load_protocol", lambda _path: protocol)
    monkeypatch.setattr("snodo.readiness.checker.assess_readiness", lambda *_a, **_k: SimpleNamespace(all_findings=[]))
    monkeypatch.setattr("snodo.cli.commands.cloud_status.sync_issue_notice", lambda: "Cloud sync needs attention: 1 session has 1,456 unsent events. Run `snodo cloud sync --all --force`.")
    text = home_view.render_home_view()
    assert "! cloud" in text
    assert "1,456 unsent events" in text


def test_renderer_does_not_write_audit_event(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    (root / ".snodo").mkdir()
    from snodo.infrastructure import paths
    monkeypatch.setattr(paths, "resolve_project_root", lambda: str(root))
    protocol = SimpleNamespace(initial_mode="producer", modes=[])
    monkeypatch.setattr("snodo.cli.commands.load_protocol", lambda _path: protocol)
    monkeypatch.setattr("snodo.readiness.checker.assess_readiness", lambda *_a, **_k: SimpleNamespace(all_findings=[]))
    monkeypatch.setattr("snodo.cli.commands.cloud_status.sync_issue_notice", lambda: None)
    home_view.render_home_view()
    assert not (root / ".snodo" / "audit.log").exists()

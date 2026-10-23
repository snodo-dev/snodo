from types import SimpleNamespace

from snodo.coders.rewrite_guard import implausible_rewrite_feedback


def test_short_existing_rewrite_refused_without_changing_disk(tmp_path):
    target = tmp_path / "large.py"
    original = "x" * 5000
    target.write_text(original)
    result = implausible_rewrite_feedback(
        [{"path": "large.py", "content": "x" * 100}],
        SimpleNamespace(project_root=tmp_path), "implement a feature",
    )
    assert "large.py" in result["large.py"]
    assert "4900" in result["large.py"]
    assert target.read_text() == original


def test_acknowledged_and_new_file_writes_are_not_refused(tmp_path):
    (tmp_path / "existing.py").write_text("x" * 5000)
    files = [
        {"path": "existing.py", "content": "small", "acknowledge_short_rewrite": True},
        {"path": "new.py", "content": "small"},
    ]
    assert implausible_rewrite_feedback(
        files, SimpleNamespace(project_root=tmp_path), "implement a feature",
    ) == {}


def test_substantial_edit_and_requested_shortening_are_not_refused(tmp_path):
    (tmp_path / "existing.py").write_text("x" * 5000)
    workspace = SimpleNamespace(project_root=tmp_path)
    assert implausible_rewrite_feedback(
        [{"path": "existing.py", "content": "x" * 3000}], workspace, "edit this file",
    ) == {}
    assert implausible_rewrite_feedback(
        [{"path": "existing.py", "content": "short"}], workspace, "Shorten this file",
    ) == {}

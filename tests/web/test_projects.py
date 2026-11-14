from pathlib import Path

from snodo.web.projects import Project, discover_projects, resolve_projects


def make_project(path: Path) -> Path:
    (path / ".snodo").mkdir(parents=True)
    return path


def test_discover_projects_returns_sorted_projects_and_ignores_unmarked(tmp_path):
    root = tmp_path / "root"
    make_project(root / "zeta")
    make_project(root / "alpha")
    (root / "ordinary").mkdir()

    projects = discover_projects([root])

    assert [project.name for project in projects] == ["alpha", "zeta"]
    assert all(project.path.is_absolute() for project in projects)


def test_discovery_does_not_search_inside_a_project(tmp_path):
    root = make_project(tmp_path / "outer")
    make_project(root / "nested")

    assert [p.name for p in discover_projects([tmp_path])] == ["outer"]


def test_discovery_skips_hidden_and_excluded_directories(tmp_path):
    root = tmp_path / "root"
    make_project(root / ".hidden" / "secret")
    make_project(root / "node_modules" / "dependency")
    make_project(root / "visible")

    assert [p.name for p in discover_projects([root])] == ["visible"]


def test_missing_root_and_duplicate_roots_are_safe(tmp_path):
    project = make_project(tmp_path / "root" / "app")

    results = discover_projects([tmp_path / "missing", tmp_path, project.parent])

    assert len(results) == 1
    assert results[0].path == project.resolve()


def test_resolve_projects_uses_cwd_only_when_roots_are_empty(tmp_path):
    project = make_project(tmp_path / "app")

    assert resolve_projects([], project / "subdir") == [
        Project(project.name, project.resolve())
    ]
    assert resolve_projects([], tmp_path / "outside") == []

"""Source checkout version labels are deterministic and fail closed."""

import subprocess

from snodo.version import _version_for


def _git(repo, *args):
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit(repo, name, content):
    (repo / name).write_text(content)
    _git(repo, "add", name)
    _git(
        repo,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "-m",
        name,
    )


def test_source_checkout_version_contains_count_and_short_sha(tmp_path):
    repo = tmp_path / "checkout"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "pyproject.toml").write_text('[project]\nversion = "0.20.0"\n')
    _commit(repo, "first", "one")
    package_dir = repo / "snodo"
    package_dir.mkdir()

    first = _version_for("0.19.3", package_dir)
    first_count = int(first.split("+b", 1)[1].split(".g", 1)[0])
    first_sha = _git(repo, "rev-parse", "--short", "HEAD")
    assert first == f"0.20.0+b{first_count}.g{first_sha}"

    _commit(repo, "second", "two")
    second = _version_for("0.19.3", package_dir)
    second_count = int(second.split("+b", 1)[1].split(".g", 1)[0])
    assert second_count == first_count + 1
    assert second.startswith("0.20.0+")
    assert second.endswith(f".g{_git(repo, 'rev-parse', '--short', 'HEAD')}")


def test_outside_git_worktree_uses_plain_version(tmp_path):
    assert _version_for("0.19.3", tmp_path) == "0.19.3"


def test_checkout_without_readable_pyproject_falls_back_to_metadata(tmp_path):
    _git(tmp_path, "init", "-q")
    _commit(tmp_path, "tracked", "content")
    package_dir = tmp_path / "snodo"
    package_dir.mkdir()
    assert _version_for("0.19.3", package_dir) == "0.19.3+b1.g" + _git(
        tmp_path, "rev-parse", "--short", "HEAD"
    )


def test_unavailable_git_uses_plain_version(tmp_path, monkeypatch):
    def unavailable(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr("snodo.version.subprocess.run", unavailable)
    assert _version_for("0.19.3", tmp_path) == "0.19.3"

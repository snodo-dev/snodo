"""Tests for scripts/select_affected_tests.py."""

import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "select_affected_tests.py"


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _repo(tmp_path: Path, files: dict[str, str]) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    for name, contents in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
    return repo, _commit(repo, "base")


def _run(repo: Path, base: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--base", base],
        cwd=repo,
        text=True,
        capture_output=True,
    )


def _change(repo: Path, name: str, contents: str) -> None:
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")


def test_changed_test_file_is_selected(tmp_path):
    repo, base = _repo(tmp_path, {"tests/cli/test_old.py": "x = 1\n"})
    _change(repo, "tests/cli/test_old.py", "x = 2\n")
    _commit(repo, "change test")
    result = _run(repo, base)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["tests/cli/test_old.py"]


def test_source_selects_tests_importing_its_module_across_directories(tmp_path):
    repo, base = _repo(
        tmp_path,
        {
            "snodo/cli/commands/plan_run.py": "x = 1\n",
            "packages/snodo-engine/src/snodo/engine/loop.py": "x = 1\n",
            "tests/other/test_plan.py": "import snodo.cli.commands.plan_run\n",
            "tests/engine/test_loop.py": "from snodo.engine import loop\n",
            "tests/test_unrelated.py": "x = 1\n",
        },
    )
    _change(repo, "snodo/cli/commands/plan_run.py", "x = 2\n")
    _change(repo, "packages/snodo-engine/src/snodo/engine/loop.py", "x = 2\n")
    _commit(repo, "change sources")
    result = _run(repo, base)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "tests/engine/test_loop.py",
        "tests/other/test_plan.py",
    ]


def test_shared_change_selects_entire_suite(tmp_path):
    repo, base = _repo(tmp_path, {"pyproject.toml": "[project]\nname='x'\n"})
    _change(repo, "pyproject.toml", "[project]\nname='y'\n")
    _commit(repo, "change config")
    result = _run(repo, base)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "tests/\n"


GUIDE_TESTS = [
    "tests/mcp/test_blocked_plan_guidance.py",
    "tests/mcp/test_server.py",
    "tests/mcp/test_structure_guidance.py",
]
GUIDE_MARKER = '<!-- snodo-guide topic="planning" summary="Plan" section="# Plan" -->\n'


def test_non_guide_docs_only_change_selects_nothing(tmp_path):
    repo, base = _repo(tmp_path, {"docs/guide.md": "before\n"})
    _change(repo, "docs/guide.md", "after\n")
    _commit(repo, "docs")
    result = _run(repo, base)
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_guide_doc_change_selects_its_consumers_only(tmp_path):
    repo, base = _repo(tmp_path, {
        "docs/authoring-a-plan.md": GUIDE_MARKER,
        "tests/mcp/test_server.py": "x = 1\n",
        "tests/mcp/test_structure_guidance.py": "x = 1\n",
        "tests/mcp/test_blocked_plan_guidance.py": "x = 1\n",
        "tests/test_unrelated.py": "x = 1\n",
    })
    _change(repo, "docs/authoring-a-plan.md", GUIDE_MARKER + "updated\n")
    _commit(repo, "guide docs")
    result = _run(repo, base)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == GUIDE_TESTS


def test_deleted_guide_doc_selects_its_consumers(tmp_path):
    repo, base = _repo(tmp_path, {
        "docs/authoring-a-plan.md": GUIDE_MARKER,
        "tests/mcp/test_server.py": "x = 1\n",
        "tests/mcp/test_structure_guidance.py": "x = 1\n",
        "tests/mcp/test_blocked_plan_guidance.py": "x = 1\n",
    })
    (repo / "docs/authoring-a-plan.md").unlink()
    _commit(repo, "delete guide docs")
    result = _run(repo, base)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == GUIDE_TESTS


def test_docs_file_without_guide_marker_selects_nothing(tmp_path):
    repo, base = _repo(tmp_path, {"docs/plain.md": "plain docs\n"})
    _change(repo, "docs/plain.md", "updated plain docs\n")
    _commit(repo, "plain docs")
    result = _run(repo, base)
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_unknown_or_missing_base_fails_clearly(tmp_path):
    repo, _ = _repo(tmp_path, {"README.md": "hello\n"})
    for base in ("missing-ref", ""):
        args = [sys.executable, str(SCRIPT)]
        if base:
            args.extend(["--base", base])
        result = subprocess.run(args, cwd=repo, text=True, capture_output=True)
        assert result.returncode != 0
        assert "base" in result.stderr.lower() or "base" in result.stdout.lower()

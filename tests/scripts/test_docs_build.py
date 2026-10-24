"""The strict documentation build remains independent of the fragment gate."""

from pathlib import Path
import subprocess
import sys

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def build(repo, destination):
    return subprocess.run(
        [sys.executable, "-m", "mkdocs", "build", "--strict", "--clean",
         "--config-file", str(repo / "mkdocs.yml"), "--site-dir", str(destination)],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )


def test_repository_docs_build(tmp_path):
    result = build(REPO_ROOT, tmp_path / "site")
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("target", ["missing.md", "/index.md", "../outside.txt"])
def test_strict_build_rejects_invalid_local_links(tmp_path, target):
    (tmp_path / "docs").mkdir()
    (tmp_path / "mkdocs.yml").write_text(
        "site_name: Canary\nstrict: true\nvalidation:\n  links:\n"
        "    not_found: warn\n    absolute_links: warn\n    unrecognized_links: warn\n",
        encoding="utf-8",
    )
    (tmp_path / "docs/index.md").write_text(
        f"# Canary\n\n[invalid]({target})\n", encoding="utf-8"
    )
    result = build(tmp_path, tmp_path / "site")
    assert result.returncode != 0
    assert target in result.stdout + result.stderr


def test_make_docs_runs_anchor_gate_before_unchanged_strict_build():
    text = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    recipe = text.split("\ndocs:\n", 1)[1].split("\ndocs-serve:", 1)[0]
    assert recipe.index("python scripts/enforce_docs_anchors.py") < recipe.index(
        "mkdocs build --strict"
    )
    workflow = (REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "run: make docs" in workflow

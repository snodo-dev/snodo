"""Tests for scripts/check_release_version.py."""

import importlib.util
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "check_release_version.py"
_spec = importlib.util.spec_from_file_location("check_release_version", SCRIPT_PATH)
release_check = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(release_check)


def _write_pyproject(path: Path, version: str) -> None:
    path.write_text(f'[project]\nname = "snodo"\nversion = "{version}"\n', encoding="utf-8")


def _write_changelog(path: Path, version: str | None) -> None:
    sections = ["# Changelog", "", "## [Unreleased]"]
    if version:
        sections.extend(["", f"## [{version}] - 2026-08-27"])
    path.write_text("\n".join(sections) + "\n", encoding="utf-8")


def test_normalize_release_tag_accepts_common_shapes():
    assert release_check.normalize_release_tag("v0.6.2") == "0.6.2"
    assert release_check.normalize_release_tag("refs/tags/v0.6.2") == "0.6.2"
    assert release_check.normalize_release_tag("0.6.2") == "0.6.2"
    assert release_check.normalize_release_tag("") == ""


def test_validate_release_accepts_matching_tag_and_changelog(tmp_path):
    pyproject = tmp_path / "pyproject.toml"
    changelog = tmp_path / "CHANGELOG.md"
    _write_pyproject(pyproject, "0.6.2")
    _write_changelog(changelog, "0.6.2")

    errors = release_check.validate_release("v0.6.2", release_check.read_pyproject_version(pyproject), changelog)

    assert errors == []


def test_validate_release_refuses_version_mismatch(tmp_path):
    pyproject = tmp_path / "pyproject.toml"
    changelog = tmp_path / "CHANGELOG.md"
    _write_pyproject(pyproject, "0.6.1")
    _write_changelog(changelog, "0.6.2")

    errors = release_check.validate_release("v0.6.2", release_check.read_pyproject_version(pyproject), changelog)

    assert len(errors) == 1
    assert "resolves to version '0.6.2'" in errors[0]
    assert "pyproject.toml declares version '0.6.1'" in errors[0]


def test_validate_release_refuses_missing_changelog_section(tmp_path):
    pyproject = tmp_path / "pyproject.toml"
    changelog = tmp_path / "CHANGELOG.md"
    _write_pyproject(pyproject, "0.6.2")
    _write_changelog(changelog, None)

    errors = release_check.validate_release("v0.6.2", release_check.read_pyproject_version(pyproject), changelog)

    assert len(errors) == 1
    assert "CHANGELOG.md has no section for version '0.6.2'" in errors[0]


def test_main_refuses_mismatch_and_missing_section(tmp_path, capsys):
    pyproject = tmp_path / "pyproject.toml"
    changelog = tmp_path / "CHANGELOG.md"
    _write_pyproject(pyproject, "0.6.1")
    _write_changelog(changelog, None)

    rc = release_check.main(
        [
            "--tag",
            "v0.6.2",
            "--pyproject",
            str(pyproject),
            "--changelog",
            str(changelog),
        ]
    )

    out = capsys.readouterr().out
    assert rc == 1
    assert "Release validation failed:" in out
    assert "pyproject.toml declares version '0.6.1'" in out
    assert "CHANGELOG.md has no section for version '0.6.2'" in out


def test_main_passes_matching_release(tmp_path, capsys):
    pyproject = tmp_path / "pyproject.toml"
    changelog = tmp_path / "CHANGELOG.md"
    _write_pyproject(pyproject, "0.6.2")
    _write_changelog(changelog, "0.6.2")

    rc = release_check.main(
        [
            "--tag",
            "v0.6.2",
            "--pyproject",
            str(pyproject),
            "--changelog",
            str(changelog),
        ]
    )

    out = capsys.readouterr().out
    assert rc == 0
    assert "Release validation passed" in out


def test_release_workflow_has_publish_gates():
    workflow = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "release.yml"
    text = workflow.read_text(encoding="utf-8")

    assert "uv run pytest tests/ -q -n auto" in text
    assert "uv run ruff check ." in text
    assert "uv run lint-imports" in text
    assert "scripts/check_release_version.py" in text
    assert "Build all packages" in text
    assert "Publish all artifacts" in text

    assert text.index("scripts/check_release_version.py") < text.index("Build all packages")
    assert text.index("uv run lint-imports") < text.index("Build all packages")

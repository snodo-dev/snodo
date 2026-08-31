"""Tests for scripts/check_release_version.py."""

import importlib.util
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "check_release_version.py"
_spec = importlib.util.spec_from_file_location("check_release_version", SCRIPT_PATH)
release_check = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(release_check)

_BODY_SCRIPT_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "changelog_release_body.py"
)
_body_spec = importlib.util.spec_from_file_location("changelog_release_body", _BODY_SCRIPT_PATH)
release_body = importlib.util.module_from_spec(_body_spec)
assert _body_spec.loader is not None
_body_spec.loader.exec_module(release_body)


def release_body_main(argv):
    return release_body.main(argv)


def _write_pyproject(path: Path, version: str) -> None:
    path.write_text(f'[project]\nname = "snodo"\nversion = "{version}"\n', encoding="utf-8")


def _write_changelog(path: Path, version: str | None) -> None:
    sections = ["# Changelog", "", "## [Unreleased]"]
    if version:
        sections.extend(
            [
                "",
                f"## [{version}] - 2026-08-27",
                "",
                "Released and verified.",
            ]
        )
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


def test_extract_changelog_section_returns_verbatim_body(tmp_path):
    changelog = tmp_path / "CHANGELOG.md"
    _write_changelog(changelog, "0.6.2")

    body = release_check.extract_changelog_section(changelog, "0.6.2")

    assert body is not None
    assert "[0.6.2]" not in body
    assert "Released and verified." in body


def test_extract_changelog_section_keeps_subsections_until_separator(tmp_path):
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(
        "\n".join(
            [
                "# Changelog",
                "",
                "## [Unreleased]",
                "",
                "---",
                "",
                "## [0.6.2] - 2026-08-27",
                "",
                "Some summary.",
                "",
                "### Fixed",
                "",
                "- First fix.",
                "- Second fix.",
                "",
                "---",
                "",
                "## [0.5.4] - 2026-08-20",
                "",
                "Older notes that must not leak in.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    body = release_check.extract_changelog_section(changelog, "0.6.2")

    assert body is not None
    assert "Some summary." in body
    assert "### Fixed" in body
    assert "- First fix." in body
    assert "- Second fix." in body
    assert "0.5.4" not in body
    assert "Older notes" not in body


def test_extract_changelog_section_missing_returns_none(tmp_path):
    changelog = tmp_path / "CHANGELOG.md"
    _write_changelog(changelog, None)

    assert release_check.extract_changelog_section(changelog, "9.9.9") is None


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


def test_release_workflow_creates_github_release():
    workflow = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "release.yml"
    text = workflow.read_text(encoding="utf-8")

    assert "Create GitHub Release" in text
    assert "gh release create" in text
    assert "scripts/changelog_release_body.py" in text
    assert "dist/*" in text
    assert "RELEASE_TAG: ${{ github.ref_name }}" in text
    assert "Publish all artifacts" in text
    assert text.index("Publish all artifacts") < text.index("Create GitHub Release")


def test_changelog_release_body_extracts_version_section(tmp_path, capsys):
    changelog = tmp_path / "CHANGELOG.md"
    _write_changelog(changelog, "0.6.2")

    rc = release_body_main(["--tag", "v0.6.2", "--changelog", str(changelog)])

    out = capsys.readouterr().out
    assert rc == 0
    assert "Released and verified." in out
    assert "[0.6.2]" not in out


def test_changelog_release_body_empty_when_no_version(tmp_path, capsys):
    rc = release_body_main(["--tag", "", "--changelog", str(tmp_path / "CHANGELOG.md")])
    assert rc == 0
    assert capsys.readouterr().out == ""


def test_changelog_release_body_empty_when_section_missing(tmp_path, capsys):
    changelog = tmp_path / "CHANGELOG.md"
    _write_changelog(changelog, None)

    rc = release_body_main(["--tag", "v0.6.2", "--changelog", str(changelog)])

    assert rc == 0
    assert capsys.readouterr().out == ""

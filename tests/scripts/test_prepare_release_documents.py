"""Tests for release changelog and security-document preparation."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "prepare_release_documents.py"
SPEC = importlib.util.spec_from_file_location("prepare_release_documents", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(module)


def _files(tmp_path: Path, notes: str = "- Release note.\n") -> tuple[Path, Path]:
    changelog = tmp_path / "CHANGELOG.md"
    security = tmp_path / "SECURITY.md"
    changelog.write_text(f"# Changelog\n\n## [Unreleased]\n\n{notes}\n## [0.20.0] — 2026-01-01\n\nOld note.\n", encoding="utf-8")
    security.write_text("| 0.20.x | ✅ |\n| < 0.20 | ❌ |\n", encoding="utf-8")
    return changelog, security


def test_promotion_preserves_notes_and_passes_release_version_check(tmp_path: Path) -> None:
    original_note = "- Release note.\n  Continued detail.\n\n"
    changelog, security = _files(tmp_path, original_note)

    module.prepare(changelog, security, "0.21.0", "minor", "2026-10-10")

    output = changelog.read_text(encoding="utf-8")
    assert f"## [Unreleased]\n\n## [0.21.0] — 2026-10-10\n\n{original_note}" in output
    assert output.count("## [Unreleased]") == 1
    assert "## [0.20.0]" in output

    release_check_path = ROOT / "scripts" / "check_release_version.py"
    check_spec = importlib.util.spec_from_file_location("release_check", release_check_path)
    release_check = importlib.util.module_from_spec(check_spec)
    assert check_spec.loader is not None
    check_spec.loader.exec_module(release_check)
    assert release_check.validate_release("v0.21.0", "0.21.0", changelog) == []


def test_empty_unreleased_fails_without_mutating_documents(tmp_path: Path) -> None:
    changelog, security = _files(tmp_path, "")
    before = changelog.read_text(encoding="utf-8")

    try:
        module.prepare(changelog, security, "0.21.0", "minor", "2026-10-10")
    except ValueError as exc:
        assert "empty" in str(exc)
    else:
        raise AssertionError("empty [Unreleased] section should fail")
    assert changelog.read_text(encoding="utf-8") == before


def test_minor_bump_updates_security_versions(tmp_path: Path) -> None:
    changelog, security = _files(tmp_path)
    module.prepare(changelog, security, "0.21.0", "minor", "2026-10-10")
    assert "| 0.21.x | ✅ |" in security.read_text(encoding="utf-8")
    assert "| < 0.21 | ❌ |" in security.read_text(encoding="utf-8")

    root = tmp_path / "repo"
    (root / "packages" / "snodo-core").mkdir(parents=True)
    (root / "pyproject.toml").write_text('[project]\nversion = "0.21.0"\n', encoding="utf-8")
    (root / "SECURITY.md").write_text(security.read_text(encoding="utf-8"), encoding="utf-8")
    (root / "docs").mkdir()
    (root / "docs" / "architecture.md").write_text("| **snodo-core** | Core |\n", encoding="utf-8")
    docs_script = ROOT / "scripts" / "enforce_release_package_docs.py"
    docs_spec = importlib.util.spec_from_file_location("release_docs", docs_script)
    release_docs = importlib.util.module_from_spec(docs_spec)
    assert docs_spec.loader is not None
    docs_spec.loader.exec_module(release_docs)
    assert release_docs.check(root) == []


def test_patch_bump_leaves_security_unchanged(tmp_path: Path) -> None:
    changelog, security = _files(tmp_path)
    before = security.read_text(encoding="utf-8")
    module.prepare(changelog, security, "0.20.1", "patch", "2026-10-10")
    assert security.read_text(encoding="utf-8") == before

"""Tests for release and package documentation drift enforcement."""

import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "enforce_release_package_docs.py"
_spec = importlib.util.spec_from_file_location("release_package_docs", SCRIPT_PATH)
check_module = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(check_module)


def _repo(root: Path) -> Path:
    (root / "packages" / "snodo-core").mkdir(parents=True)
    (root / "packages" / "snodo-tools").mkdir()
    (root / "pyproject.toml").write_text('[project]\nversion = "0.19.3"\n', encoding="utf-8")
    (root / "SECURITY.md").write_text(
        "| 0.19.x | ✅ |\n| < 0.19 | ❌ |\n", encoding="utf-8"
    )
    (root / "docs").mkdir()
    (root / "docs" / "architecture.md").write_text(
        "| **snodo-core** | Core |\n| **snodo-tools** | Tools |\n", encoding="utf-8"
    )
    return root


def test_current_release_and_workspace_docs_pass(tmp_path: Path) -> None:
    assert check_module.check(_repo(tmp_path)) == []


def test_supported_and_unsupported_version_drift_names_security_file(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "SECURITY.md").write_text("| 0.7.x | ✅ |\n| < 0.7 | ❌ |\n", encoding="utf-8")

    failures = check_module.check(root)

    assert len(failures) == 2
    assert all("SECURITY.md" in failure for failure in failures)
    assert all("0.19" in failure for failure in failures)


def test_package_table_drift_names_missing_and_extra_values(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "packages" / "snodo-engine").mkdir()
    (root / "docs" / "architecture.md").write_text(
        "| **snodo-core** | Core |\n| **snodo-stale** | Old |\n", encoding="utf-8"
    )

    failures = check_module.check(root)

    assert len(failures) == 1
    assert "docs/architecture.md" in failures[0]
    assert "snodo-engine" in failures[0]
    assert "snodo-stale" in failures[0]

"""Verification gate canaries proving that gates fail when violations are injected.

FILE: tests/golden/test_verification_canaries.py (Fixes #58)

A gate that has never been observed failing is not known to gate.
This module provides explicit canary tests for each verification gate:
- import-linter: a deliberate forbidden upward import in a fixture package must break a contract
- ruff: a fixture file with a known lint violation (unused import) must fail linting
- toolchain pin: a dependency declared with a range ('>=') instead of '==' must be rejected
"""

import subprocess
import sys
import tempfile
from pathlib import Path

from importlinter.api import use_cases


def test_import_linter_canary_detects_forbidden_upward_import():
    """Canary: import-linter must detect a forbidden upward import and report broken contract."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        pkg = tmp_path / "canary_pkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        mod_a = pkg / "mod_a.py"
        mod_b = pkg / "mod_b.py"
        mod_b.write_text("VALUE = 42\n")
        # Deliberate upward/forbidden import: mod_a imports mod_b
        mod_a.write_text("from canary_pkg import mod_b\n")

        cfg = tmp_path / ".importlinter"
        cfg.write_text("""[importlinter]
root_package = canary_pkg

[importlinter:contract:canary-forbidden]
name = mod_a forbidden from importing mod_b
type = forbidden
source_modules = canary_pkg.mod_a
forbidden_modules = canary_pkg.mod_b
""")

        sys.path.insert(0, str(tmp_path))
        try:
            passed = use_cases.lint_imports(config_filename=str(cfg))
            assert passed is False, "import-linter failed to catch a forbidden upward import"
        finally:
            if str(tmp_path) in sys.path:
                sys.path.remove(str(tmp_path))


def test_ruff_canary_detects_lint_violation():
    """Canary: ruff check must fail (non-zero exit code) when a file contains lint errors."""
    with tempfile.TemporaryDirectory() as tmp:
        bad_file = Path(tmp) / "bad_fixture.py"
        # Deliberate lint violation: unused imports F401
        bad_file.write_text("import sys\nimport os\n")

        proc = subprocess.run(
            ["uv", "run", "ruff", "check", str(bad_file)],
            capture_output=True,
            text=True,
        )
        assert proc.returncode != 0, "ruff check failed to detect a lint violation"
        assert "F401" in proc.stdout or "unused import" in proc.stdout.lower()


def test_toolchain_pin_canary_detects_unpinned_dependency():
    """Canary: toolchain pin guard must reject a dependency declared without exact '==' pin."""
    from tests.golden.test_toolchain_pin import _parse_requirement

    # Test exact pin vs range specifier
    name, exact_spec = _parse_requirement("pytest==9.0.3")
    assert exact_spec is not None and exact_spec.startswith("==")

    name, range_spec = _parse_requirement("pytest>=9.0.0")
    assert range_spec is not None and not range_spec.startswith("=="), (
        "toolchain pin parser failed to detect unpinned '>=' range specifier"
    )

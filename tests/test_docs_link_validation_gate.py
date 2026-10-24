"""Prove the strict documentation build rejects unresolved local links."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_strict_docs_build_fails_on_missing_local_link(tmp_path: Path) -> None:
    """The production link-validation settings must fail on an unresolved target."""
    production_config = yaml.safe_load((ROOT / "mkdocs.yml").read_text())
    config = {
        "site_name": "Link validation fixture",
        "strict": production_config["strict"],
        "validation": production_config["validation"],
        "docs_dir": str(tmp_path / "docs"),
        "site_dir": str(tmp_path / "site"),
        "theme": {"name": "mkdocs"},
        "nav": [{"Home": "index.md"}, {"Canary": "canary.md"}],
    }
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "index.md").write_text("# Fixture\n")
    (docs_dir / "canary.md").write_text(
        "# Canary\n\n[missing target](link-validation-missing-page.md)\n"
    )
    config_path = tmp_path / "mkdocs.yml"
    config_path.write_text(yaml.safe_dump(config))

    result = subprocess.run(
        [sys.executable, "-m", "mkdocs", "build", "--config-file", str(config_path)],
        capture_output=True,
        text=True,
        check=False,
    )

    output = result.stdout + result.stderr
    assert result.returncode != 0, output
    assert "link-validation-missing-page.md" in output, output
    assert "not included in the navigation" not in output, output

"""Golden-file test infrastructure.

FILE: tests/golden/conftest.py (Task 7.13)

Deterministic snapshot testing for shipped protocol templates.
"""

import json
import os
from pathlib import Path

import pytest


@pytest.fixture
def update_goldens() -> bool:
    """Check if goldens should be regenerated instead of compared."""
    return os.environ.get("SNODO_UPDATE_GOLDENS") == "1"


@pytest.fixture
def snapshots_dir() -> Path:
    """Resolve the snapshots directory."""
    return Path(__file__).parent / "snapshots"


def _load_protocol(template_name: str):
    """Load a shipped template as a parsed Protocol."""
    import yaml
    from snodo.compiler.models import Protocol

    template_path = Path(__file__).parent.parent.parent / "snodo" / "protocols" / "templates" / f"{template_name}.yml"
    data = yaml.safe_load(template_path.read_text())
    return Protocol(**data)


def verify_golden(template_name: str, snapshots_dir: Path, update: bool) -> None:
    """Verify a protocol template matches its golden snapshot."""
    golden_path = snapshots_dir / f"{template_name}.golden.json"
    protocol = _load_protocol(template_name)

    # Serialize to deterministic JSON
    actual = json.loads(protocol.model_dump_json())
    serialized = json.dumps(actual, indent=2, sort_keys=True)

    if update:
        golden_path.write_text(serialized + "\n")
        return

    if not golden_path.exists():
        raise FileNotFoundError(
            f"Golden file not found: {golden_path}\n"
            f"Run with SNODO_UPDATE_GOLDENS=1 to generate it."
        )

    expected = golden_path.read_text().strip()
    if serialized.strip() != expected:
        # Show a meaningful diff
        raise AssertionError(
            f"Golden file mismatch for {template_name}.yml\n"
            f"  Golden: {golden_path}\n"
            f"  To update: SNODO_UPDATE_GOLDENS=1 pytest"
        )

"""Drift test for the released protocol schema publication."""

import importlib.util
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "protocol_schema.py"
spec = importlib.util.spec_from_file_location("protocol_schema", SCRIPT_PATH)
protocol_schema = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(protocol_schema)


def test_protocol_schema_matches_committed_publication():
    expected = json.loads(protocol_schema.SCHEMA_PATH.read_text(encoding="utf-8"))
    actual = protocol_schema.publication()
    assert actual == expected, (
        "Protocol schema publication drifted. Regenerate it with "
        "`uv run python scripts/protocol_schema.py --write` and review the diff."
    )

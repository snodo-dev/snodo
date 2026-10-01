"""Drift test for the released protocol schema publication."""

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "protocol_schema.py"
SCHEMA_PATH = REPO_ROOT / "schemas" / "protocol-v1.json"


def test_protocol_schema_matches_committed_publication():
    # Generate in a fresh interpreter. The publication reads process-global
    # registries (validator types, coders), and other tests in the same pytest
    # worker register fake validator types into them, which leaked into the
    # live schema on GitHub Actions and failed the 0.19.1 release.
    proc = subprocess.run(
        [sys.executable, str(SCRIPT_PATH)],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    actual = json.loads(proc.stdout)
    expected = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert actual == expected, (
        "Protocol schema publication drifted. Regenerate it with "
        "`uv run python scripts/protocol_schema.py --write` and review the diff."
    )

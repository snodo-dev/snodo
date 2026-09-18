#!/usr/bin/env python
"""Emit the schemas of the two published cloud payloads.

The schemas are derived from the payload declarations in the infrastructure
modules. This command deliberately contains no transcribed field list: the
declaration is the interface, and this is its machine-readable publication.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from pydantic import TypeAdapter

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from snodo.infrastructure.cloud_liveness import LivenessSnapshot  # noqa: E402
from snodo.infrastructure.cloud_sync import AuditIngestBatch  # noqa: E402


def published_schemas() -> dict[str, dict]:
    """Return the current schemas for both cloud payloads."""
    return {
        "cloud_ingest": TypeAdapter(AuditIngestBatch).json_schema(),
        "cloud_liveness": TypeAdapter(LivenessSnapshot).json_schema(),
    }


def main() -> int:
    print(json.dumps(published_schemas(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

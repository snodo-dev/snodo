#!/usr/bin/env python3
"""Fail when a literal audit event emission is missing from the cloud wire."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from snodo.infrastructure.cloud_sync import _EVENT_DATA_KEYS  # noqa: E402


def emitted_event_types(roots: tuple[Path, ...]) -> set[str]:
    """Collect event names passed literally to audit emission APIs."""
    emitted: set[str] = set()
    for root in roots:
        for path in root.rglob("*.py"):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except (OSError, SyntaxError) as exc:
                print(f"Cannot inspect audit emitter source {path}: {exc}", file=sys.stderr)
                raise
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if not isinstance(node.func, ast.Attribute):
                    continue
                if node.func.attr not in {"append_event", "_audit", "_log_event"} or not node.args:
                    continue
                event_type = node.args[0]
                if isinstance(event_type, ast.Constant) and isinstance(event_type.value, str):
                    emitted.add(event_type.value)
    return emitted


def main() -> int:
    roots = (REPO_ROOT / "packages", REPO_ROOT / "snodo")
    emitted = emitted_event_types(roots)
    undeclared = sorted(emitted - set(_EVENT_DATA_KEYS))
    if undeclared:
        for event_type in undeclared:
            print(f"FAIL: audit event {event_type!r} is emitted but absent from the cloud ingest contract")
        return 1
    print(f"Audit event contract check OK: all {len(emitted)} literal emitted types are declared.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

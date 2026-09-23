#!/usr/bin/env python
"""Enforce the published cloud payload schemas against silent shape changes.

The schemas are DERIVED from the payload declarations by
``cloud_payload_schema.py`` and compared with the recorded publication in
``scripts/cloud_schema_baseline.json``. A field addition or removal is named
separately because consumers experience those as different changes. Either is
a version-bump decision, not an edit. An intentional interface change
increments the published interface version and updates the baseline in the
same change. There is no inline suppression or exemption.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from cloud_payload_schema import published_schemas  # noqa: E402

BASELINE_RELATIVE_PATH = Path("scripts") / "cloud_schema_baseline.json"


class Report:
    """The schema changes found against the published baseline."""

    def __init__(self) -> None:
        self.added: list[tuple[str, str]] = []
        self.removed: list[tuple[str, str]] = []


def _field_paths(schema: dict, prefix: str = "") -> set[str]:
    paths: set[str] = set()
    for name, value in schema.get("properties", {}).items():
        path = f"{prefix}.{name}" if prefix else name
        paths.add(path)
        for nested in value.get("anyOf", []):
            paths |= _field_paths(nested, path)
        if "$ref" in value:
            paths.add(f"{path}.*")
    for name, value in schema.get("$defs", {}).items():
        paths |= _field_paths(value, f"{prefix}{name}.")
    return paths


def check(current: dict[str, dict], baseline: dict[str, dict]) -> Report:
    """Report added and removed field paths for each published payload."""
    report = Report()
    for payload in sorted(set(current) | set(baseline)):
        now = _field_paths(current.get(payload, {}))
        old = _field_paths(baseline.get(payload, {}))
        report.added.extend((payload, field) for field in sorted(now - old))
        report.removed.extend((payload, field) for field in sorted(old - now))
    return report


def _message(kind: str, payload: str, field: str) -> str:
    return (
        f"{payload} payload field {field!r} was {kind}. "
        "Changing the published interface is a version-bump decision, not an edit."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".")
    parser.add_argument("--baseline", default=None)
    parser.add_argument("--update-baseline", action="store_true")
    args = parser.parse_args(argv)
    repo_root = Path(args.repo).resolve()
    baseline_path = Path(args.baseline) if args.baseline else repo_root / BASELINE_RELATIVE_PATH
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    report = check(published_schemas(), baseline)
    if args.update_baseline:
        baseline_path.write_text(
            json.dumps(published_schemas(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return 0
    for payload, field in report.added:
        print(f"FAIL: {_message('added', payload, field)}")
    for payload, field in report.removed:
        print(f"FAIL: {_message('removed', payload, field)}")
    if report.added or report.removed:
        return 1
    print("Published cloud schema check OK: all fields are baselined.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

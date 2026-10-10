#!/usr/bin/env python
"""Promote pending release notes and refresh SECURITY.md for a release."""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path


def prepare(changelog_path: Path, security_path: Path, version: str, part: str, released: str) -> None:
    changelog = changelog_path.read_text(encoding="utf-8")
    match = re.search(r"(?m)^## \[Unreleased\][ \t]*\n", changelog)
    if match is None:
        raise ValueError("CHANGELOG.md has no [Unreleased] section")
    next_heading = re.search(r"(?m)^## ", changelog[match.end():])
    end = match.end() + next_heading.start() if next_heading else len(changelog)
    body = changelog[match.end():end]
    if not body.strip():
        raise ValueError("CHANGELOG.md [Unreleased] section is empty; refusing release")
    promoted = f"## [{version}] — {released}\n" + body
    changelog_path.write_text(changelog[:match.start()] + promoted + changelog[end:], encoding="utf-8")

    if part != "patch":
        security = security_path.read_text(encoding="utf-8")
        major, minor, *_ = version.split(".")
        security = re.sub(r"(?m)^\| \d+\.\d+\.x \| ✅ \|$", f"| {major}.{minor}.x | ✅ |", security, count=1)
        security = re.sub(r"(?m)^\| < \d+\.\d+ \| ❌ \|$", f"| < {major}.{minor} | ❌ |", security, count=1)
        security_path.write_text(security, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--part", choices=("major", "minor", "patch"), required=True)
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument("--changelog", type=Path, default=Path("CHANGELOG.md"))
    parser.add_argument("--security", type=Path, default=Path("SECURITY.md"))
    args = parser.parse_args(argv)
    try:
        prepare(args.changelog, args.security, args.version, args.part, args.date)
    except (OSError, ValueError) as exc:
        print(f"Release document preparation failed: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

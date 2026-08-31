#!/usr/bin/env python
"""Emit a version's CHANGELOG.md section as GitHub Release notes.

The release workflow validates the changelog gate before tests run
(``check_release_version.py`` — the section is guaranteed to exist), then
builds this script's output into the ``gh release create --notes`` body.

Returns an empty string when the section is missing so the release step never
silently fails a publish that has already passed validation.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_release_version import extract_changelog_section, normalize_release_tag


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Emit a version's CHANGELOG.md section as release notes."
    )
    parser.add_argument(
        "--tag",
        default=os.environ.get("GITHUB_REF_NAME", ""),
        help="Release tag, e.g. v0.6.2. Defaults to GITHUB_REF_NAME.",
    )
    parser.add_argument(
        "--changelog",
        default="CHANGELOG.md",
        help="Path to CHANGELOG.md.",
    )
    args = parser.parse_args(argv)

    version = normalize_release_tag(args.tag)
    if not version:
        print("", end="")
        return 0

    body = extract_changelog_section(Path(args.changelog), version)
    print(body or "", end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())

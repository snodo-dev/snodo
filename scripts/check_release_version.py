#!/usr/bin/env python
"""Validate a release tag against pyproject.toml and CHANGELOG.md.

This script is called by the release workflow before publishing. It exists so
the tag/version/changelog gate is a testable unit instead of an unproven
workflow expression. A release gate nobody has seen fail is not a gate.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import tomllib
from pathlib import Path


def normalize_release_tag(tag: str) -> str:
    """Return the semantic version from ``v1.2.3`` or ``refs/tags/v1.2.3``."""
    cleaned = (tag or "").strip()
    if cleaned.startswith("refs/tags/"):
        cleaned = cleaned.removeprefix("refs/tags/")
    if cleaned.startswith("v"):
        cleaned = cleaned[1:]
    return cleaned


def read_pyproject_version(pyproject_path: Path) -> str:
    """Read ``project.version`` from a pyproject.toml file."""
    with pyproject_path.open("rb") as fh:
        data = tomllib.load(fh)
    return str(data["project"]["version"])


def changelog_has_version_section(changelog_path: Path, version: str) -> bool:
    """Return True if CHANGELOG.md contains a Keep-a-Changelog section."""
    text = changelog_path.read_text(encoding="utf-8")
    pattern = rf"^#+\s+\[{re.escape(version)}\]"
    return re.search(pattern, text, re.MULTILINE) is not None


def validate_release(
    tag: str,
    pyproject_version: str,
    changelog_path: Path,
) -> list[str]:
    """Return release gate errors for a tag/version/changelog mismatch."""
    version = normalize_release_tag(tag)
    errors: list[str] = []

    if not version:
        errors.append("No release tag provided; refusing publish.")
    else:
        if version != pyproject_version:
            errors.append(
                f"Release tag {tag!r} resolves to version {version!r}, "
                f"but pyproject.toml declares version {pyproject_version!r}."
            )
        if not changelog_has_version_section(changelog_path, version):
            errors.append(
                f"CHANGELOG.md has no section for version {version!r}; "
                "add a released section before publishing."
            )

    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check that a release tag matches pyproject.toml and CHANGELOG.md."
    )
    parser.add_argument(
        "--tag",
        default=os.environ.get("GITHUB_REF_NAME", ""),
        help="Release tag, e.g. v0.6.2. Defaults to GITHUB_REF_NAME.",
    )
    parser.add_argument(
        "--pyproject",
        default="pyproject.toml",
        help="Path to pyproject.toml.",
    )
    parser.add_argument(
        "--changelog",
        default="CHANGELOG.md",
        help="Path to CHANGELOG.md.",
    )
    args = parser.parse_args(argv)

    pyproject_path = Path(args.pyproject)
    changelog_path = Path(args.changelog)

    if not pyproject_path.is_file():
        print(f"Release validation failed: pyproject file not found: {pyproject_path}")
        return 1
    if not changelog_path.is_file():
        print(f"Release validation failed: CHANGELOG file not found: {changelog_path}")
        return 1

    try:
        pyproject_version = read_pyproject_version(pyproject_path)
    except Exception as exc:
        print(f"Release validation failed: could not read project.version from {pyproject_path}: {exc}")
        return 1

    errors = validate_release(args.tag, pyproject_version, changelog_path)
    if errors:
        print("Release validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    print(
        f"Release validation passed: tag {args.tag!r} resolves to version "
        f"{normalize_release_tag(args.tag)!r}, matching pyproject.toml and CHANGELOG.md."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

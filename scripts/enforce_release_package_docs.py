#!/usr/bin/env python
"""Keep release support and architecture package documentation in sync."""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path

PACKAGE_ROW = re.compile(r"^\| \*\*(snodo-[^*]+)\*\* \|", re.MULTILINE)


def check(repo_root: Path) -> list[str]:
    """Return documentation drift errors derived from project metadata/layout."""
    with (repo_root / "pyproject.toml").open("rb") as source:
        project = tomllib.load(source)
    version = project["project"]["version"]
    major, minor, *_ = version.split(".")
    series = f"{major}.{minor}.x"
    threshold = f"< {major}.{minor}"

    security_path = repo_root / "SECURITY.md"
    security = security_path.read_text(encoding="utf-8")
    failures: list[str] = []
    if f"| {series} | ✅ |" not in security:
        failures.append(
            f"SECURITY.md: supported version must be `{series}` to match "
            f"pyproject.toml version `{version}`; update the supported-versions table."
        )
    if f"| {threshold} | ❌ |" not in security:
        failures.append(
            f"SECURITY.md: unsupported version cutoff must be `{threshold}` "
            f"to match pyproject.toml version `{version}`; update the supported-versions table."
        )

    expected = {path.name for path in (repo_root / "packages").iterdir() if path.is_dir()}
    architecture_path = repo_root / "docs" / "architecture.md"
    architecture = architecture_path.read_text(encoding="utf-8")
    documented = set(PACKAGE_ROW.findall(architecture))
    if documented != expected:
        missing = sorted(expected - documented)
        extra = sorted(documented - expected)
        failures.append(
            "docs/architecture.md: package table disagrees with packages/; "
            f"add {missing or 'none'} and remove {extra or 'none'}."
        )
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".", help="Repository root (default: current directory).")
    args = parser.parse_args(argv)
    try:
        failures = check(Path(args.repo).resolve())
    except (KeyError, OSError, tomllib.TOMLDecodeError) as exc:
        print(f"Release/package documentation check failed: {exc}")
        return 1
    for failure in failures:
        print(f"FAIL: {failure}")
    if failures:
        return 1
    print("Release/package documentation check OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

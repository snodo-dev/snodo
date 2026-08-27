#!/usr/bin/env python
"""Enforce the patch-coverage threshold in CI (ADR 032).

Lives in a file rather than inline in .github/workflows/ci.yml. The inline
version embedded unindented Python inside a YAML block scalar, which silently
made the whole workflow invalid: every run failed to start, with no log and no
test output, for as long as it was on main. Nothing noticed, because the local
gates pass on a repository whose workflow file cannot be parsed.

A script is also testable and lintable, which the inline form was not.
"""

import sys

from snodo.infrastructure.patch_coverage import (
    calculate_patch_coverage,
    enforce_patch_coverage,
    parse_coverage_xml,
    parse_git_diff_added_lines,
)

THRESHOLD = 80.0


def main() -> int:
    base, added = parse_git_diff_added_lines(".")
    coverage = parse_coverage_xml("coverage.xml")
    result = calculate_patch_coverage(added, coverage, base)
    ok, message = enforce_patch_coverage(result, THRESHOLD)
    print(message)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

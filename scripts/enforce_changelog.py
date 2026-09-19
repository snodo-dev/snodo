#!/usr/bin/env python
"""Require issue-closing commits in the checked branch range to be documented.

The check deliberately judges a range, not the repository's whole history.
The caller supplies the base commit for the branch being verified; commits
before that base were already accepted by an earlier gate and are not this
change's author's responsibility. CI supplies the pull request base, while
``make gate`` passes the local branch's merge-base with ``origin/main``.

Only commit messages that claim to close an issue are considered. Merge,
revert, and release commits are excluded because they do not represent a new
authorial change that owes the changelog an explanation. The changelog itself
is never generated: the author must write the explanation and mention the
issue number in it, and put it in the pending section rather than a dated
release section.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path


ISSUE_CLOSING = re.compile(
    r"\b(?:fix(?:e[sd])?|close[sd]?|resolve[sd]?)\s+#(\d+)\b", re.IGNORECASE
)
ISSUE_REFERENCE = re.compile(r"\B#(\d+)\b")
SECTION = re.compile(r"(?m)^## (.+?)\s*$")
PENDING_SECTION = re.compile(r"^\[Unreleased\]$", re.IGNORECASE)
DATED_SECTION = re.compile(r"^\[[^\]]+\]\s+[—-]\s+\d{4}-\d{2}-\d{2}$")


class ChangelogCheckError(Exception):
    """The git range could not be established or read."""


def _git(repo_root: Path, *args: str) -> str:
    try:
        return subprocess.run(  # noqa: S603, S607 - executable and arguments are fixed below
            ["git", *args],  # noqa: S607 - git is the fixed executable
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = exc.stderr.strip() if isinstance(exc, subprocess.CalledProcessError) else str(exc)
        raise ChangelogCheckError(f"git {' '.join(args)} failed: {detail}") from exc


def _commits(repo_root: Path, base: str) -> list[tuple[str, str, str, int]]:
    records = _git(
        repo_root,
        "log",
        "--format=%H%x00%P%x00%s%x00%B%x1e",
        f"{base}..HEAD",
    ).split("\x1e")
    commits: list[tuple[str, str, str, int]] = []
    for record in records:
        if not record.strip():
            continue
        fields = record.rstrip("\n").split("\x00", 3)
        if len(fields) != 4:
            raise ChangelogCheckError("git log returned an unreadable commit record")
        sha, parents, subject, message = fields
        commits.append((sha, subject, message, len(parents.split()) if parents else 0))
    return commits


def _closing_issues(subject: str, message: str, parent_count: int) -> set[str]:
    if parent_count > 1 or subject.lower().startswith(("revert ", "release:")):
        return set()
    return {match.group(1) for match in ISSUE_CLOSING.finditer(f"{subject}\n{message}")}


def _issue_sections(changelog: str) -> dict[str, list[str]]:
    matches = list(SECTION.finditer(changelog))
    sections: dict[str, list[str]] = {}
    for index, match in enumerate(matches):
        name = match.group(1).strip()
        body_end = matches[index + 1].start() if index + 1 < len(matches) else len(changelog)
        for issue_match in ISSUE_REFERENCE.finditer(changelog, match.end(), body_end):
            sections.setdefault(issue_match.group(1), []).append(name)
    return sections


def check(repo_root: Path, base: str, changelog_path: Path) -> list[str]:
    """Return one failure for every issue-closing commit lacking a changelog reference."""
    changelog = changelog_path.read_text(encoding="utf-8")
    issue_sections = _issue_sections(changelog)
    failures: list[str] = []
    for sha, subject, message, parent_count in _commits(repo_root, base):
        for issue in sorted(_closing_issues(subject, message, parent_count), key=int):
            sections = issue_sections.get(issue, [])
            if not sections:
                failures.append(
                    f"commit {sha[:12]} closes issue #{issue} but CHANGELOG.md does not "
                    f"mention #{issue}; add a changelog entry explaining what changed."
                )
                continue
            pending = [section for section in sections if PENDING_SECTION.fullmatch(section)]
            if pending:
                continue
            released = [section for section in sections if DATED_SECTION.fullmatch(section)]
            if released:
                failures.append(
                    f"commit {sha[:12]} closes issue #{issue} but its CHANGELOG entry is in "
                    f"released section {released[0]!r}; add it under [Unreleased]."
                )
            else:
                failures.append(
                    f"commit {sha[:12]} closes issue #{issue} but its CHANGELOG entry is in "
                    f"section {sections[0]!r}; add it under [Unreleased]."
                )
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Require issue-closing commits in a branch range to be documented."
    )
    parser.add_argument("--repo", default=".", help="Repository root (default: current directory).")
    parser.add_argument(
        "--base",
        default=os.environ.get("CHANGELOG_BASE", "origin/main"),
        help="Base commit for this branch range (default: CHANGELOG_BASE or origin/main).",
    )
    parser.add_argument("--changelog", default="CHANGELOG.md", help="Path to CHANGELOG.md.")
    args = parser.parse_args(argv)
    repo_root = Path(args.repo).resolve()
    changelog_path = repo_root / args.changelog

    try:
        failures = check(repo_root, args.base, changelog_path)
    except (ChangelogCheckError, OSError) as exc:
        print(f"Changelog check failed: {exc}")
        return 1
    for failure in failures:
        print(f"FAIL: {failure}")
    if failures:
        print(f"Changelog check failed: {len(failures)} undocumented issue-closing commit(s).")
        return 1
    print(f"Changelog check OK: commits after {args.base} are documented.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

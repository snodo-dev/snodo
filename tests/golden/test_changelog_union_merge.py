"""Canary: CHANGELOG.md must merge cleanly when parallel branches both append.

FILE: tests/golden/test_changelog_union_merge.py

Every agent appends its entry at the top of ``### Added`` under
``[Unreleased]``, so any two branches collide on the same three lines by
construction. Six agent merges produced six conflicts, all in CHANGELOG.md,
all resolved identically by keeping both entries (issue #81).

Fragments (``changelog.d/`` + an assembly step) solve this "properly" but cost
an assembly step and the single readable CHANGELOG in the working tree. We
chose the cheaper, correct-direction fix instead: ``.gitattributes`` marks
CHANGELOG.md with git's built-in ``merge=union`` driver, so a parallel append
merges as "keep both" with no conflict and identical entries dedupe.

Canary rule (Fixes #58): a gate that has never been observed failing cannot be
trusted to gate. This test proves the driver is configured AND that its
absence is what a conflicted CHANGELOG merge looks like — if someone drops the
``.gitattributes`` line, this fails at the branch instead of at the merge.
"""

import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
ATTRIBUTES = ROOT / ".gitattributes"


def _git(repo: Path, *args: str):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def test_changelog_has_union_merge_driver():
    """CHANGELOG.md is declared merge=union, so parallel appends keep both."""
    text = ATTRIBUTES.read_text()
    for line in text.splitlines():
        # git format: `<pattern> <attr>=<value>`  (e.g. `CHANGELOG.md merge=union`)
        parts = line.split()
        if len(parts) < 2 or parts[0] != "CHANGELOG.md":
            continue
        attr, _, value = parts[1].partition("=")
        if attr == "merge" and value == "union":
            return
    raise AssertionError(
        "CHANGELOG.md is not declared `merge=union` in .gitattributes. Without "
        "it, every parallel merge conflicts on the top-of-[Unreleased] lines "
        "(issue #81). Add: `CHANGELOG.md merge=union`"
    )


def test_without_union_driver_parallel_changelog_merge_conflicts():
    """Without the driver, two top-appends to CHANGELOG.md conflict — proving
    the canary fails when the gate is removed, and that the driver is what
    prevents the observed failure."""
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        _git(repo, "init", "-q", "-b", "main")
        _git(repo, "config", "user.email", "t@t.com")
        _git(repo, "config", "user.name", "T")
        changelog = repo / "CHANGELOG.md"
        changelog.write_text(
            "# Changelog\n\n## [Unreleased]\n\n### Added\n\n- Existing entry.\n"
        )
        _git(repo, "add", ".")
        _git(repo, "commit", "-qm", "base")

        def append_entry(entry: str):
            s = changelog.read_text()
            changelog.write_text(
                s.replace(
                    "### Added\n\n- Existing",
                    f"### Added\n\n{entry}\n\n- Existing",
                    1,
                )
            )

        # Branch A appends its entry.
        _git(repo, "checkout", "-qb", "agent-a")
        append_entry("- Entry from A (Fixes #1).")
        _git(repo, "add", "CHANGELOG.md")
        _git(repo, "commit", "-qm", "A")

        # Branch B appends its entry to the same base.
        _git(repo, "checkout", "-q", "main")
        _git(repo, "checkout", "-qb", "agent-b")
        append_entry("- Entry from B (Fixes #2).")
        _git(repo, "add", "CHANGELOG.md")
        _git(repo, "commit", "-qm", "B")

        _git(repo, "checkout", "-q", "main")
        _git(repo, "merge", "-q", "agent-a", "--no-edit")

        # No union driver → the second parallel append conflicts.
        proc = subprocess.run(
            ["git", "merge", "agent-b", "--no-edit"],
            cwd=repo, capture_output=True, text=True,
        )
        assert proc.returncode != 0, (
            "A parallel CHANGELOG append merged without conflict even though "
            "CHANGELOG.md is not merge=union. The canary assumes union is what "
            "prevents the conflict; if this passes, re-examine the assumption."
        )


def test_union_driver_makes_parallel_changelog_merge_keep_both():
    """With the driver, the same scenario merges cleanly and keeps both entries
    (and dedupes identical entries) — the behaviour the real merge path relies
    on."""
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        _git(repo, "init", "-q", "-b", "main")
        _git(repo, "config", "user.email", "t@t.com")
        _git(repo, "config", "user.name", "T")
        (repo / ".gitattributes").write_text("CHANGELOG.md merge=union\n")
        changelog = repo / "CHANGELOG.md"
        changelog.write_text(
            "# Changelog\n\n## [Unreleased]\n\n### Added\n\n- Existing entry.\n"
        )
        _git(repo, "add", ".")
        _git(repo, "commit", "-qm", "base")

        def append_entry(entry: str):
            s = changelog.read_text()
            changelog.write_text(
                s.replace(
                    "### Added\n\n- Existing",
                    f"### Added\n\n{entry}\n\n- Existing",
                    1,
                )
            )

        _git(repo, "checkout", "-qb", "agent-a")
        append_entry("- Entry from A (Fixes #1).")
        _git(repo, "add", "CHANGELOG.md")
        _git(repo, "commit", "-qm", "A")

        _git(repo, "checkout", "-q", "main")
        _git(repo, "checkout", "-qb", "agent-b")
        append_entry("- Entry from B (Fixes #2).")
        _git(repo, "add", "CHANGELOG.md")
        _git(repo, "commit", "-qm", "B")

        _git(repo, "checkout", "-q", "main")
        _git(repo, "merge", "-q", "agent-a", "--no-edit")
        _git(repo, "merge", "-q", "agent-b", "--no-edit")

        merged = changelog.read_text()
        assert "- Entry from A (Fixes #1)." in merged
        assert "- Entry from B (Fixes #2)." in merged
        assert merged.count("- Existing entry.") == 1


def test_union_driver_dedupes_identical_entries():
    """Two branches writing the same entry (same issue) collapse to one, so the
    changelog does not gain noise from the parallel fix being merged twice."""
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        _git(repo, "init", "-q", "-b", "main")
        _git(repo, "config", "user.email", "t@t.com")
        _git(repo, "config", "user.name", "T")
        (repo / ".gitattributes").write_text("CHANGELOG.md merge=union\n")
        changelog = repo / "CHANGELOG.md"
        changelog.write_text(
            "# Changelog\n\n## [Unreleased]\n\n### Added\n\n- Existing entry.\n"
        )
        _git(repo, "add", ".")
        _git(repo, "commit", "-qm", "base")

        def append_entry(entry: str):
            s = changelog.read_text()
            changelog.write_text(
                s.replace(
                    "### Added\n\n- Existing",
                    f"### Added\n\n{entry}\n\n- Existing",
                    1,
                )
            )

        _git(repo, "checkout", "-qb", "agent-a")
        append_entry("- Same entry both agents (Fixes #9).")
        _git(repo, "add", "CHANGELOG.md")
        _git(repo, "commit", "-qm", "A")

        _git(repo, "checkout", "-q", "main")
        _git(repo, "checkout", "-qb", "agent-b")
        append_entry("- Same entry both agents (Fixes #9).")
        _git(repo, "add", "CHANGELOG.md")
        _git(repo, "commit", "-qm", "B")

        _git(repo, "checkout", "-q", "main")
        _git(repo, "merge", "-q", "agent-a", "--no-edit")
        _git(repo, "merge", "-q", "agent-b", "--no-edit")

        merged = changelog.read_text()
        assert merged.count("- Same entry both agents (Fixes #9).") == 1

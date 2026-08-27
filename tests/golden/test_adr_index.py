"""ADR index and numbering conformance.

FILE: tests/golden/test_adr_index.py

Two agents working in parallel each pick the next ADR number from their own
worktree, and both land on the same one. It has happened three times (023,
028, a 030/031 near miss), each time surfacing as a merge conflict in
docs/decisions/README.md plus a renamed file whose internal heading still
carries the old number.

This test pins three facts that make the collision visible at the branch
instead of the merge:

- every ``NNN-*.md`` file's number matches the number in its own heading
  (so a rename that forgets to update the heading fails),
- every number appears exactly once in the index (so two files claiming the
  same number fail),
- every index entry resolves to a real file and every ADR file is indexed
  (so a merged file missing from the index, or a stale link, fails).
"""

import re
from pathlib import Path

DOCS = Path(__file__).parent.parent.parent / "docs"
DECISIONS = DOCS / "decisions"
INDEX = DECISIONS / "README.md"

# Filenames: `NNN-kebab-case.md`.  Heading formats in use:
#   # ADR NNN — Title        (modern)
#   ## NNN: Title            (legacy, with `adr: NNN` front matter)
FILE_PREFIX = re.compile(r"^(?P<num>\d{3})-(?P<slug>.+)\.md$")
HEADING_NEW = re.compile(r"^# ADR (?P<num>\d{3}) —")
HEADING_LEGACY = re.compile(r"^## (?P<num>\d{3}):")
FRONT_MATTER_ADR = re.compile(r"^adr:\s*(?P<num>\d{3})\s*$")
# Index row: `| [NNN](NNN-slug.md) | Title | ... |`
INDEX_ROW = re.compile(r"^\|\s*\[(?P<num>\d{3})\]\((?P<path>[0-9a-z-]+\.md)\)\s*\|")


def _adr_files() -> list[Path]:
    return sorted(DECISIONS.glob("[0-9][0-9][0-9]-*.md"))


def _heading_number(path: Path) -> int:
    """Return the ADR number declared in *path*'s heading, or None."""
    text = path.read_text()
    for line in text.splitlines()[:20]:
        m = HEADING_NEW.search(line)
        if m:
            return int(m.group("num"))
        m = HEADING_LEGACY.search(line)
        if m:
            return int(m.group("num"))
    return None


def test_every_adr_file_number_matches_its_heading():
    """A renamed file must not carry a stale number in its heading."""
    for path in _adr_files():
        m = FILE_PREFIX.match(path.name)
        assert m, f"ADR file does not match NNN-slug.md: {path.name}"
        file_num = int(m.group("num"))
        heading_num = _heading_number(path)
        assert heading_num is not None, (
            f"{path.name}: no 'ADR NNN' or 'NNN:' heading found in the first "
            "20 lines"
        )
        assert heading_num == file_num, (
            f"{path.name}: file is numbered {file_num} but its heading says "
            f"{heading_num:03d} — a rename that forgot to update the heading "
            "(or a heading copied from a different ADR)."
        )


def test_every_adr_number_appears_exactly_once_in_the_index():
    """Two files claiming the same number must fail here, not at the merge."""
    index_text = INDEX.read_text()
    seen: dict[int, str] = {}
    for row in index_text.splitlines():
        m = INDEX_ROW.match(row)
        if not m:
            continue
        num = int(m.group("num"))
        path = m.group("path")
        assert num not in seen, (
            f"ADR {num:03d} is indexed twice: {seen[num]} and {path}. Two "
            "agents likely claimed the same number. Re-number one of them and "
            "fix its heading and filename."
        )
        seen[num] = path


def test_adr_index_matches_files_on_disk():
    """Every ADR file is indexed, and every index link resolves."""
    index_text = INDEX.read_text()
    indexed = {
        m.group("num"): m.group("path")
        for line in index_text.splitlines()
        if (m := INDEX_ROW.match(line))
    }

    files = {
        FILE_PREFIX.match(f.name).group("num"): f.name for f in _adr_files()
    }
    for num, filename in sorted(files.items()):
        assert num in indexed, (
            f"ADR {num} ({filename}) is missing from the index — add a row to "
            f"{INDEX.relative_to(DOCS.parent)}."
        )
        assert indexed[num] == filename, (
            f"ADR {num} index link '{indexed[num]}' does not match the file "
            f"'{filename}' on disk."
        )

    for num, filename in sorted(indexed.items()):
        assert (DECISIONS / filename).exists(), (
            f"Index row for ADR {num} links to '{filename}' which does not "
            "exist on disk."
        )


def test_adr_index_is_in_number_order():
    """Index rows should be ascending so a duplicate is a local diff."""
    nums = [
        int(m.group("num"))
        for line in INDEX.read_text().splitlines()
        if (m := INDEX_ROW.match(line))
    ]
    assert nums == sorted(nums), (
        "ADR index is not in ascending numeric order; insert the new row in "
        "the right place so a renumbering is a one-line local change."
    )

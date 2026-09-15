"""Does a recovery spec still describe the tree it will be run against?

A recovery spec carries the original intent forward verbatim.  If an earlier
attempt fixed the very thing that intent cites, the next coder is sent to find
a defect that is gone — the observed case spent an hour proving the premise
false before running out of time (Fixes #286).

A spec is prose and cannot be mechanically diffed against code.  But a spec
that *asserts the presence of a code construct* is making a checkable claim:
"The booking field is `<input type="url">`" is true or false about the tree.
This module extracts those claims and answers the one question the engine may
ask — is the asserted construct still somewhere in the tree? — without ever
deciding what the task now means.  The engine reports a stale premise; it does
not rewrite the spec (#35 draws the same boundary for non-spec critique).

The check is deliberately narrow, because a false "your spec is stale" is
worse than no check:

* Only a *presence assertion* counts — a copula or a stative verb immediately
  before a code construct ("is", "contains", "has", ...).  A goal ("add
  `<input type="url">`") or a requirement ("must use ...") asserts nothing
  about the current tree and is never flagged.
* A modal before the verb ("should have", "must contain") disqualifies the
  claim: it states an obligation, not an observation.
* The construct must be a distinctive code literal — an inline-code span or an
  angle-bracket tag — never a bare quoted English word.
* Absence is checked against the whole tree, with whitespace normalised, so a
  construct that merely moved or was reformatted is not called gone.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional

# A relative file path, anchored by a code/document extension so a domain-like
# token (cal.com/you) or a version (1.0.0) is never mistaken for one.
_PATH_RE = re.compile(
    r"(?<![\w./-])"
    r"((?:[\w.-]+/)+[\w.-]+\.(?:py|js|ts|tsx|jsx|html|css|json|yml|yaml|toml|md|sh|rs|go|java|rb|php|txt|cfg|ini|env))"
    r"(?![\w./-])"
)

# An inline-code span, the unambiguous "this is a literal" sigil.
_BACKTICK_RE = re.compile(r"`([^`\n]+)`")

# An angle-bracket tag such as <input type="url">.  Quoted HTML in prose is
# usually not backticked, and the surrounding quotes cannot be matched reliably
# (the construct itself contains quotes), so the tag shape is read directly.
_TAG_RE = re.compile(r"<([A-Za-z/][^<>\n]*)>")

# A construct must carry code punctuation or whitespace.  A bare identifier
# (``oldName``) is how a rename task names the thing it is changing, not a
# structural claim about the tree, and must never be read as stale.
_CODE_PUNCT = frozenset("<>(){}[]=;:")


def _is_code_construct(token: str) -> bool:
    return len(token) >= 3 and (
        any(ch in _CODE_PUNCT for ch in token) or any(ch.isspace() for ch in token)
    )


# Words that assert the construct exists *now*, as opposed to stating a goal or
# an obligation.  "use"/"read"/"call" are deliberately absent: "must use X" is
# a requirement, not a claim about the tree.
_PRESENCE_VERB_RE = re.compile(
    r"\b(is|are|was|were|contains?|has|have|shows?|renders?|includes?)\b"
    r"(?:\s+(?:still|currently|now|already|presently))?\s*$",
    re.IGNORECASE,
)

# A modal before the presence verb turns an observation into an obligation.
_MODAL_RE = re.compile(
    r"\b(should|must|shall|can|could|will|would|may|might|ought|need|needs|"
    r"going|expected|required)\s*$",
    re.IGNORECASE,
)

# Directories never worth scanning: output, vendored code and the git store.
_SKIP_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        "dist",
        "build",
        "target",
        "site-packages",
        ".tox",
        ".nox",
        ".hypothesis",
    }
)

# A tree scan is bounded so a recovery spawn can never stall on a huge repo.
# Hitting a bound is treated as "could not prove absence" — never as absence.
_MAX_FILES = 5000
_MAX_BYTES_PER_FILE = 2_000_000


@dataclass(frozen=True)
class Citation:
    """A checkable presence claim: *path* is asserted to contain *construct*."""

    path: Optional[str]
    construct: str
    line: str

    def describe(self) -> str:
        if self.path:
            return f"{self.path!r} contains {self.construct!r}"
        return f"the tree contains {self.construct!r}"


def _is_path_token(token: str) -> bool:
    """True when *token* is a relative file path with a code extension."""
    return bool(_PATH_RE.fullmatch(token.strip()))


def _constructs_in(line: str) -> List[tuple[str, int]]:
    """Return ``(construct, position)`` for each code literal on *line*."""
    found: List[tuple[str, int]] = []
    seen: set[str] = set()
    for match in _BACKTICK_RE.finditer(line):
        token = match.group(1).strip()
        if (
            token
            and _is_code_construct(token)
            and not _is_path_token(token)
            and token not in seen
        ):
            found.append((token, match.start()))
            seen.add(token)
    for match in _TAG_RE.finditer(line):
        token = match.group(0).strip()
        if token and token not in seen:
            found.append((token, match.start()))
            seen.add(token)
    return found


def _asserts_presence(before: str) -> bool:
    """True when the text immediately before a construct claims it exists now."""
    verb_match = _PRESENCE_VERB_RE.search(before)
    if verb_match is None:
        return False
    prefix = before[: verb_match.start()]
    return not _MODAL_RE.search(prefix)


def _paths_in(line: str) -> List[str]:
    """Distinct relative file paths named on *line*, in order of appearance."""
    paths: List[str] = []
    for match in _PATH_RE.finditer(line):
        token = match.group(1).strip()
        if token and token not in paths:
            paths.append(token)
    return paths


def extract_citations(spec: str) -> List[Citation]:
    """Extract the presence claims a spec makes about code in the tree.

    A claim is a code construct introduced by a presence verb.  The file path,
    when one appears on the same line, is recorded for the report but does not
    scope the check: absence is a property of the whole tree, so a construct
    that merely moved is not called stale.
    """
    citations: List[Citation] = []
    seen: set[tuple[Optional[str], str]] = set()
    for raw_line in (spec or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        paths = _paths_in(line)
        for construct, position in _constructs_in(line):
            if not _asserts_presence(line[:position]):
                continue
            # Pair with a path only when the line names exactly one; several
            # paths make the association ambiguous, so it is left unanchored.
            path = paths[0] if len(paths) == 1 else None
            key = (path, construct)
            if key in seen:
                continue
            seen.add(key)
            citations.append(Citation(path=path, construct=construct, line=line))
    return citations


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def _iter_text_files(root: Path) -> Iterable[Path]:
    """Yield candidate text files under *root*, skipping vendor/output dirs."""
    stack = [root]
    while stack:
        directory = stack.pop()
        try:
            entries = list(directory.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.is_dir():
                if entry.name in _SKIP_DIRS or entry.name.startswith("."):
                    continue
                stack.append(entry)
            elif entry.is_file():
                if entry.name.startswith("."):
                    continue
                yield entry


def _scan_tree(root: Path, constructs: List[str]) -> tuple[dict, bool]:
    """Search *root* once for every construct.

    Returns ``(found, conclusive)``.  ``found`` maps each construct to whether
    it was seen; ``conclusive`` is False when the scan could not cover the tree
    (the file bound was reached, or a file too large to read was skipped), in
    which case a False in ``found`` means "not proven absent", not "absent".
    """
    found = {construct: False for construct in constructs}
    remaining = {construct for construct in constructs if construct.strip()}
    if not remaining:
        return found, False

    scanned = 0
    inconclusive = False
    for path in _iter_text_files(root):
        scanned += 1
        if scanned > _MAX_FILES:
            inconclusive = True
            break
        try:
            if path.stat().st_size > _MAX_BYTES_PER_FILE:
                inconclusive = True
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "\x00" in text:
            continue

        normalised_text: Optional[str] = None
        for construct in list(remaining):
            needle = construct.strip()
            if needle in text:
                found[construct] = True
                remaining.discard(construct)
            elif any(ch.isspace() for ch in needle):
                if normalised_text is None:
                    normalised_text = _normalise(text)
                if _normalise(needle) in normalised_text:
                    found[construct] = True
                    remaining.discard(construct)
        if not remaining:
            break

    return found, not inconclusive


def find_stale_citations(spec: str, root: Path) -> List[Citation]:
    """Return the presence claims in *spec* the tree no longer satisfies.

    An empty list means every checkable claim still holds — or that none was
    made, or that absence could not be proven.  The engine prefers a missed
    stale spec to a false one, so a claim is only reported stale when a
    complete scan failed to find its construct anywhere in the tree.
    """
    root = Path(root)
    if not root.is_dir():
        return []
    citations = extract_citations(spec)
    if not citations:
        return []
    found, conclusive = _scan_tree(root, [c.construct for c in citations])
    if not conclusive:
        return []
    return [c for c in citations if not found.get(c.construct, False)]

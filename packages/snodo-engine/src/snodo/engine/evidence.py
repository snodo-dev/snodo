"""Concrete anchors a task spec carries, kept across a redefinition.

A spec validator that judges wording can send a spec back to be reauthored
(ADR 023).  The reauthored spec replaces the original for every later validator
and for the coder, so anything the rewriter drops is gone from the run.  The
observed failure is what it dropped: a spec citing the file and line where a
symptom was seen came back with neither, and with its scope widened to the
ground the citation had bounded; the next validator then escalated on a task
vaguer than the one it was rewritten from (Fixes #320).

A redefinition restates what is being asked; it does not discard what the
author knew.  This module extracts the concrete, checkable anchors a spec
carries — file paths, ``path:line`` citations, line references, named tests and
backticked code literals.  The rewriter is told to reword the ask around them,
and a rewrite that dropped one is repaired with the original anchor appended
before anyone downstream sees it.  A spec with no such anchor is left exactly
as the rewriter produced it, so the no-evidence path is unchanged.
"""

from __future__ import annotations

import re
from typing import Iterable, List

# Extensions that make a token unmistakably a file path.  Kept in step with the
# tree's own notion of source (``snodo.engine.premise``) so a domain name or a
# version (cal.com/you, 1.0.0) is never read as evidence.
_EXTENSIONS = (
    r"py|pyi|js|jsx|ts|tsx|mjs|cjs|html|htm|css|scss|json|ya?ml|toml|"
    r"md|rst|sh|bash|zsh|rs|go|java|kt|rb|php|txt|cfg|ini|env|sql|proto|lock"
)
_FILE = rf"(?:[\w.-]+/)*[\w.-]+\.(?:{_EXTENSIONS})"

# A path bound to a line (or line range), the shape that bounds a scope: the
# citation names where the symptom was seen and so how far the task reaches.
_PATH_LINE_RE = re.compile(
    rf"(?<![\w./-])({_FILE}(?::\d+(?:-\d+)?|:\d+:\d+))",
    re.IGNORECASE,
)
# The same bound written in words: "src/booking.py line 42", "line 42 of
# src/booking.py" leaves the path unattached, but "... booking.py, lines 42-45"
# is one anchor.
_PATH_LINE_WORD_RE = re.compile(
    rf"(?<![\w./-])({_FILE}\s*(?:,|:|at|on)?\s*lines?\s+\d+(?:\s*[-\u2013]\s*\d+)?)",
    re.IGNORECASE,
)
# A bare relative path, anchored by a code extension.  A sentence-ending period
# is allowed while a longer extension (``foo.py.bak``) is not.
_PATH_RE = re.compile(rf"(?<![\w./-])({_FILE})(?!\.?[\w/-])", re.IGNORECASE)
# A line reference not adjacent to a path.
_LINE_REF_RE = re.compile(r"\blines?\s+\d+(?:\s*[-\u2013]\s*\d+)?", re.IGNORECASE)
# A named test, the anchor a reproduction is pinned to.
_TEST_RE = re.compile(r"\btest_[A-Za-z0-9_]+\b")
# An inline-code span is the unambiguous "this is a literal" sigil.
_BACKTICK_RE = re.compile(r"`([^`\n]+)`")

_HEADING = "Evidence carried from the original task:"


def _add(anchors: List[str], token: str) -> None:
    """Append *token* unless a longer anchor already captures it."""
    token = token.strip()
    if not token:
        return
    if any(token in existing for existing in anchors):
        return
    anchors.append(token)


def extract_evidence(spec: str) -> List[str]:
    """The concrete anchors *spec* carries, in order of appearance.

    Longest shapes are read first so a ``path:line`` citation is one anchor
    rather than a path and a line separately; a shorter token that a longer
    anchor already contains is subsumed.  The list is empty when the spec
    carries no checkable evidence, which is how the caller tells "reword this"
    from "reword this around something".
    """
    text = spec or ""
    anchors: List[str] = []
    for match in _PATH_LINE_RE.finditer(text):
        _add(anchors, match.group(1))
    for match in _PATH_LINE_WORD_RE.finditer(text):
        _add(anchors, match.group(1))
    for match in _BACKTICK_RE.finditer(text):
        _add(anchors, match.group(1))
    for match in _TEST_RE.finditer(text):
        _add(anchors, match.group(0))
    for match in _LINE_REF_RE.finditer(text):
        _add(anchors, match.group(0))
    for match in _PATH_RE.finditer(text):
        _add(anchors, match.group(1))
    return anchors


def evidence_missing(authored: str, evidence: Iterable[str]) -> List[str]:
    """The anchors in *evidence* the *authored* spec does not carry."""
    text = authored or ""
    return [token for token in evidence if token not in text]


def preserve_evidence(authored: str, missing: Iterable[str]) -> str:
    """Return *authored* with the *missing* anchors carried forward verbatim.

    Nothing is added when nothing is missing, so a rewrite that kept the
    evidence — and a spec that had none — is returned byte-for-byte as the
    author produced it.
    """
    missing = [anchor for anchor in missing if anchor.strip()]
    if not missing:
        return authored
    block = "\n".join(f"- {anchor}" for anchor in missing)
    base = (authored or "").rstrip()
    return f"{base}\n\n{_HEADING}\n{block}"


__all__ = ["extract_evidence", "evidence_missing", "preserve_evidence"]

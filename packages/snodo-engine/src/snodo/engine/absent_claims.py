"""Detect explicit file-absence claims contradicted by a workspace's HEAD.

This is best-effort evidence only; it does not decide or halt an engine run.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

# Deliberately recognize only explicit absence wording paired with a quoted,
# backticked, or conventional source-file path.
_ABSENCE = re.compile(
    r"\b(?:does not exist|doesn't exist|doesnt exist|not present|missing|"
    r"absent|nonexistent|does not have|cannot find|can't find)\b",
    re.IGNORECASE,
)
_QUOTED_PATH = re.compile(r"[`\"']([^`\"'\n]+)[`\"']")
_BARE_PATH = re.compile(
    r"(?<![\w./-])((?:[\w.-]+/)+[\w.-]+\.[A-Za-z0-9_-]+|"
    r"[\w.-]+\.[A-Za-z0-9_-]+)(?![\w./-])"
)


@dataclass(frozen=True)
class AbsentClaimContradiction:
    """Paths reported absent but tracked in the checked-out HEAD."""

    paths: tuple[str, ...]
    head: str


def detect_absent_claims(report: str, workspace: str | Path) -> AbsentClaimContradiction | None:
    """Return explicit missing-path claims contradicted by *workspace* HEAD.

    Only paths in HEAD count (working-tree-only/untracked files do not).
    Invalid repositories and malformed reports fail closed with ``None``.
    """
    if not isinstance(report, str) or not report.strip():
        return None
    root = Path(workspace).resolve()
    try:
        head = _git(root, "rev-parse", "HEAD").strip()
        names = set(_git(root, "ls-tree", "-r", "--name-only", "-z", "HEAD").split("\0"))
    except (OSError, subprocess.CalledProcessError):
        return None

    candidates: set[str] = set()
    # Keep claims local: a nearby path in another sentence is unrelated.
    for sentence in re.split(r"(?<=[.!?])\s+|[\r\n]+", report):
        absence_matches = list(_ABSENCE.finditer(sentence))
        if not absence_matches:
            continue
        for pattern in (_QUOTED_PATH, _BARE_PATH):
            for path_match in pattern.finditer(sentence):
                path_start, path_end = path_match.span()
                path = path_match.group(1).strip()
                for absence in absence_matches:
                    if path_end <= absence.start():
                        between = sentence[path_end:absence.start()]
                        relation = re.fullmatch(r"\s+(?:(?:is|was|seems)\s+)?", between, re.I)
                    elif absence.end() <= path_start:
                        between = sentence[absence.end():path_start]
                        relation = re.fullmatch(r"\s+(?:(?:file|path)\s+)?", between, re.I)
                        if sentence[absence.start():absence.end()].lower() != "missing":
                            relation = bool(relation)
                        elif relation:
                            relation = bool(relation)
                    else:
                        relation = False
                    if relation:
                        candidates.add(path)
                        break

    contradicted: set[str] = set()
    for raw in candidates:
        normalized = raw.replace("\\", "/")
        relative = PurePosixPath(normalized)
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            continue
        canonical = relative.as_posix()
        if canonical in names:
            contradicted.add(canonical)
    if not contradicted:
        return None
    return AbsentClaimContradiction(tuple(sorted(contradicted)), head)


def _git(root: Path, *args: str) -> str:
    return subprocess.run(  # noqa: S603
        ["git", "-C", str(root), *args],  # noqa: S607
        check=True,
        capture_output=True,
        text=True,
    ).stdout

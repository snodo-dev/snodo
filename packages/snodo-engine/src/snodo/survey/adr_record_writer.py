"""Write accepted ADR proposals as numbered decision records."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from snodo.survey.adr_proposals import ADRProposal

_NUMBERED_RECORD = re.compile(r"^(\d{3})-.*\.md$")


def _slug(title: str) -> str:
    """Return a filesystem-safe, readable slug for a decision title."""
    normalized = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", normalized.lower()).strip("-")
    return slug or "decision"


def _render(proposal: ADRProposal, number: int) -> str:
    evidence = "\n".join(
        f"- `{reference.path}`"
        + (f" (lines {reference.line_range[0]}–{reference.line_range[1]})" if reference.line_range else "")
        for reference in proposal.evidence
    ) or "- None provided."
    return (
        f"# ADR {number:03d} — {proposal.title.strip()}\n\n"
        "## Status\n\nProposed\n\n"
        f"## Context\n\n{proposal.context.strip()}\n\n"
        f"## Decision\n\n{proposal.decision.strip()}\n\n"
        f"## Consequences\n\n{proposal.consequences.strip()}\n\n"
        "## Alternatives considered\n\nNot specified in proposal.\n\n"
        f"## Evidence\n\n{evidence}\n"
    )


def write_adr_record(proposal: ADRProposal, decisions_dir: str | Path) -> Path:
    """Write *proposal* to the next available numbered ADR file.

    The file is created exclusively, so an existing record is never replaced.
    Returns the path of the newly written record.
    """
    folder = Path(decisions_dir)
    folder.mkdir(parents=True, exist_ok=True)
    highest = max(
        (int(match.group(1)) for path in folder.iterdir()
         if (match := _NUMBERED_RECORD.match(path.name))),
        default=0,
    )
    number = highest + 1
    slug = _slug(proposal.title)
    while True:
        target = folder / f"{number:03d}-{slug}.md"
        try:
            with target.open("x", encoding="utf-8") as record:
                record.write(_render(proposal, number))
            return target
        except FileExistsError:
            number += 1

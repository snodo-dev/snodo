"""Propose validator criteria from a repository's own decision records.

FILE: snodo/survey/criteria.py

Survey derives the extension of governance from code — which modules exist,
how each is verified, where decisions live — and deliberately derives none of
the protocol's normative content, because that content is prose a human wrote.
But the prose is in the repository. A decision record that states the rule the
protocol encodes by hand is exactly the source intake never opens, and this
module opens it.

What makes a sentence proposable
--------------------------------

The rule is section membership, and nothing subtler. A decision record written
in the Nygard shape separates what was *decided* from why it was decided and
what followed. Only the ``## Decision`` section states the rule; the other
sections describe the record rather than constrain the code:

* ``Context`` is background — the situation that made a decision necessary.
  Reading it as a requirement would turn a description of a problem into a
  demand.
* ``Consequences`` states what follows *because* of the decision. A
  consequence is an effect a reader can observe, not a rule anyone can
  violate, so proposing it would ask the operator to reject a true sentence.
* ``Alternatives considered`` names options that were rejected. A rejected
  alternative is the opposite of a rule; proposing one would ask the operator
  to add the road not taken.
* ``Status`` is metadata about the record, not a rule about the repository.

So a record with no ``## Decision`` section proposes nothing, and a record
whose Decision section holds only headings, code fences or a bare link
proposes nothing either. "Not every record carries a criterion" is a fact
about the records, and the proposal pass reports it by proposing nothing
rather than by inventing a requirement to fill the gap.

Within the Decision section every bullet item and every standalone paragraph
is a proposal, each carrying the record it came from. The pass deliberately
does not judge whether a decision sentence *ought* to be a validator
criterion; that is the operator's question, answered one proposal at a time.
What it does judge is attribution: a proposal without the record it came from
is not made at all.

The citation discipline is the one a boundary judge already uses on source
files: a claim carries the file it rests on, and the file is resolved against
the repository before the claim is accepted. Here the claim is a criterion,
the file is the record, and ``_resolve_citation`` (imported, not reimplemented)
does the resolving, so there is one way in this codebase to say "this came
from there" rather than two.

Nothing here writes. ``propose_criteria`` reads; ``append_criteria`` is a pure
transformation of a protocol mapping; the caller decides whether, and when, to
put bytes on disk. The engine cannot write to the protocol on its own.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from snodo.survey.analyzer import _detect_decision_paths, _resolve_citation

# A short statement is a fragment ("See above."), not a criterion worth an
# operator's attention. The threshold is deliberately low: it drops noise, not
# judgement.
_MIN_CRITERION_CHARS = 24

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.*)$")
_FENCE_RE = re.compile(r"^\s*(?:```|~~~)")
_DECISION_HEADING_RE = re.compile(r"^decision\b", re.IGNORECASE)
_ENUMERATION_RE = re.compile(r"^\d+[.)]\s+")
_ADR_PREFIX_RE = re.compile(r"^(?:ADR\s*)?\d+\s*[—–-]?\s*", re.IGNORECASE)
_TABLE_ROW_RE = re.compile(r"^\s*\|")


@dataclass(frozen=True)
class CriterionProposal:
    """A rule sentence from a decision record, offered as a validator criterion.

    ``criterion`` is the rule in the record's own words. ``record_path`` is the
    repository-relative citation the proposal stands on — a proposal without it
    is never constructed. ``record_title`` and ``record_section`` are for the
    operator reading the proposal, not for the protocol.
    """

    criterion: str
    record_path: str
    record_title: str
    record_section: str

    @property
    def citation(self) -> str:
        return self.record_path

    def to_dict(self) -> Dict[str, Any]:
        return {
            "criterion": self.criterion,
            "record_path": self.record_path,
            "record_title": self.record_title,
            "record_section": self.record_section,
        }


class UnknownValidatorError(ValueError):
    """Accepted criteria named a validator the protocol does not declare."""


def _record_title(text: str) -> str:
    """The record's title, with its ADR number stripped when present."""
    for line in text.splitlines():
        match = _HEADING_RE.match(line)
        if not match:
            continue
        title = match.group(2).strip()
        stripped = _ADR_PREFIX_RE.sub("", title, count=1).strip()
        return stripped or title
    return ""


def _decision_section(text: str) -> Optional[tuple[str, List[str]]]:
    """The lines under the record's ``## Decision`` heading, and its text.

    Returns ``None`` when the record has no Decision section — the record
    states no rule this pass will propose. Only the first Decision heading is
    read; a second one would be part of the same decision in ordinary prose.
    """
    heading = ""
    body: List[str] = []
    capturing = False
    for line in text.splitlines():
        match = _HEADING_RE.match(line)
        if match:
            if capturing:
                break
            if _DECISION_HEADING_RE.match(match.group(2).strip()):
                capturing = True
                heading = match.group(2).strip()
            continue
        if capturing:
            body.append(line)
    if not capturing:
        return None
    return heading, body


def _clean_criterion(raw: str) -> str:
    """Collapse a markdown statement to the prose a criterion is written in."""
    text = raw.replace("**", "").replace("__", "")
    text = re.sub(r"\s+", " ", text).strip()
    text = _ENUMERATION_RE.sub("", text, count=1)
    text = text.strip("*_ ")
    return text.strip()


def _is_proposable(text: str) -> bool:
    """A cleaned statement worth an operator's decision, or noise to drop."""
    if len(text) < _MIN_CRITERION_CHARS:
        return False
    if not any(ch.isalpha() for ch in text):
        return False
    # A bare link is a pointer, not a rule; a bare path likewise.
    if re.fullmatch(r"\[[^\]]*\]\([^)]*\)", text):
        return False
    return not text.startswith("http")


def _statements(body_lines: Sequence[str]) -> List[str]:
    """The bullet items and paragraphs of a section, in reading order.

    Code fences are examples rather than rules and are skipped whole; a table
    row is data, not a statement. A bullet may wrap onto continuation lines and
    is joined; a paragraph runs until a blank line, a bullet, or a fence.
    """
    statements: List[str] = []
    block: Optional[List[str]] = None
    fenced = False

    def flush() -> None:
        nonlocal block
        if block:
            text = _clean_criterion(" ".join(block))
            if _is_proposable(text):
                statements.append(text)
        block = None

    for raw in body_lines:
        if _FENCE_RE.match(raw):
            flush()
            fenced = not fenced
            continue
        if fenced:
            continue
        if not raw.strip():
            flush()
            continue
        if _TABLE_ROW_RE.match(raw):
            flush()
            continue
        bullet = _BULLET_RE.match(raw)
        if bullet:
            flush()
            block = [bullet.group(1)]
            continue
        if block is None:
            block = [raw.strip()]
        else:
            block.append(raw.strip())
    flush()
    return statements


def _proposals_from_record(
    project_root: Path, record_rel: str, record_path: str
) -> List[CriterionProposal]:
    """Propose the rules one record states, each citing that record."""
    text = (project_root / record_rel).read_text(errors="replace")
    section = _decision_section(text)
    if section is None:
        return []
    heading, body = section
    title = _record_title(text)
    return [
        CriterionProposal(
            criterion=statement,
            record_path=record_path,
            record_title=title,
            record_section=heading,
        )
        for statement in _statements(body)
    ]


def propose_criteria(
    project_root: Path,
    decision_paths: Optional[Sequence[str]] = None,
) -> List[CriterionProposal]:
    """Propose criteria from every decision record under *project_root*.

    ``decision_paths`` defaults to the directories survey already discovers
    (``docs/decisions``, ``docs/adr`` and their siblings), so intake and survey
    agree about where the records are. Every proposed criterion cites the
    record file it came from, and the citation is resolved against the
    repository with the analyzer's own discipline before the proposal is made:
    a criterion whose record cannot be found is not proposed.
    """
    root = Path(project_root)
    if decision_paths is None:
        decision_paths = _detect_decision_paths(root)

    proposals: List[CriterionProposal] = []
    for rel_dir in decision_paths:
        directory = root / rel_dir
        if not directory.is_dir():
            continue
        for record in sorted(directory.glob("*.md")):
            record_rel = f"{rel_dir}/{record.name}"
            resolved = _resolve_citation(root, rel_dir, record_rel)
            if resolved is None:
                continue
            proposals.extend(_proposals_from_record(root, record_rel, resolved))
    return proposals


def select_validator(
    protocol_data: Dict[str, Any], requested: Optional[str] = None
) -> str:
    """The validator accepted criteria are appended to.

    An explicit ``requested`` id wins and must exist. Otherwise the
    ``architecture`` validator is preferred, then any validator of type
    ``architecture``, then the first declared validator. The default is a
    convenience, not a judgement about the record: the operator can always
    name the validator the criteria belong to.
    """
    validators = [
        entry
        for entry in protocol_data.get("validators", [])
        if isinstance(entry, dict)
    ]
    if requested is not None:
        if any(entry.get("validator_id") == requested for entry in validators):
            return requested
        declared = ", ".join(
            str(entry.get("validator_id")) for entry in validators
        )
        raise UnknownValidatorError(
            f"the protocol declares no validator '{requested}'; declared: {declared}"
        )
    for entry in validators:
        if entry.get("validator_id") == "architecture":
            return "architecture"
    for entry in validators:
        if entry.get("validator_type") == "architecture":
            return str(entry.get("validator_id"))
    if not validators:
        raise UnknownValidatorError("the protocol declares no validators")
    return str(validators[0].get("validator_id"))


def append_criteria(
    protocol_data: Dict[str, Any],
    validator_id: str,
    criteria: Sequence[str],
) -> Dict[str, Any]:
    """A copy of *protocol_data* with *criteria* appended to *validator_id*.

    Pure: the argument is not mutated and no file is touched, so a caller can
    build the accepted protocol without committing to writing it. Criteria
    already present are not added twice; order is preserved.
    """
    data = copy.deepcopy(protocol_data)
    for entry in data.get("validators", []):
        if not isinstance(entry, dict) or entry.get("validator_id") != validator_id:
            continue
        existing = entry.get("criteria")
        if not isinstance(existing, list):
            existing = []
        for criterion in criteria:
            if criterion not in existing:
                existing.append(criterion)
        entry["criteria"] = existing
        return data
    raise UnknownValidatorError(
        f"the protocol declares no validator '{validator_id}'"
    )

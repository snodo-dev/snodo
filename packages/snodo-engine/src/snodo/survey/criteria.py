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
* ``Status`` is metadata about the record, not a rule about the repository —
  with one exception, not an interpretation of a value: a record that says its
  decision has been *superseded* is a record whose rule is no longer in force.
  Supersession is not a status to weigh, it is a statement to honour: the
  proposal pass proposes nothing from a record in that state. The superseding
  record says so (``Supersedes ADR 1``) and the superseded one usually says so
  too (``Superseded by ADR 2``, in its Status or even its title); either
  statement is enough to know. Every other status — accepted, proposed, or one
  this pass cannot read — is metadata and changes nothing.

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
_STATUS_HEADING_RE = re.compile(r"^status\b", re.IGNORECASE)
_INLINE_STATUS_RE = re.compile(
    r"^\s*(?:[-*+]\s*)?\*{0,2}Status\*{0,2}\s*:\s*(.*)$", re.IGNORECASE
)
_SUPERSEDED_RE = re.compile(r"\bsuperseded\b", re.IGNORECASE)
_SUPERSEDES_RE = re.compile(r"\bsupersedes\b", re.IGNORECASE)
_REF_NUMBER_RE = re.compile(r"(?:ADR[\s-]*)?(\d+)", re.IGNORECASE)
_REF_PATH_RE = re.compile(r"[\w.-]+\.md")


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


def _status_text(text: str) -> Optional[str]:
    """The record's status statement, or ``None`` when none can be read.

    Status is metadata and this pass reads as little of it as possible: only a
    ``## Status`` section or a ``Status:`` line (inline or bulleted) counts as
    a place the record states its own state. Returning ``None`` means the
    record states no status here — deliberately distinct from a status that is
    present and readable, because an unreadable status is not a superseded one.
    """
    inline: List[str] = []
    heading_body: List[str] = []
    capturing = False
    for line in text.splitlines():
        match = _HEADING_RE.match(line)
        if match:
            if capturing:
                break
            if _STATUS_HEADING_RE.match(match.group(2).strip()):
                capturing = True
            continue
        if capturing:
            heading_body.append(line)
        else:
            inline_match = _INLINE_STATUS_RE.match(line)
            if inline_match:
                inline.append(inline_match.group(1))
    if capturing:
        joined = " ".join(heading_body).strip()
        if joined:
            return joined
        return None
    if inline:
        return " ".join(inline).strip() or None
    return None


def _title_line(text: str) -> str:
    """The record's title heading line, the first place it can state a state."""
    for line in text.splitlines():
        if _HEADING_RE.match(line):
            return line
    return ""


def _supersession_scope(text: str) -> str:
    """The statements of the record's own state: its title and its status.

    Deliberately narrow. A decision's prose may use the word "supersede" to
    describe an approach, not to declare a state, and reading claims there
    would drop live records; a title or status that says ``superseded`` is the
    record speaking about itself. A status that cannot be read contributes
    nothing — an unreadable status is not a superseded one.
    """
    return _title_line(text) + "\n" + (_status_text(text) or "")


def _is_superseded(scope: str) -> bool:
    """Whether the record's own statements say its decision is no longer here.

    Only the single word ``superseded`` counts. No other status value is
    interpreted — a record's status is otherwise metadata, and this is one
    specific state with one specific meaning.
    """
    return bool(_SUPERSEDED_RE.search(scope))


def _supersedes_targets(scope: str) -> List[str]:
    """Record citations the record declares it supersedes.

    A record that replaces another says so (``Supersedes ADR 002 — …``), and
    that statement is enough to know the cited record is no longer in force.
    Each target is returned in the form the citation writes it (a number like
    ``2`` or ``0002``, or a ``*.md`` name); the caller decides which sibling
    record, if any, it names.
    """
    targets: List[str] = []
    for line in scope.splitlines():
        match = _SUPERSEDES_RE.search(line)
        if not match:
            continue
        tail = line[match.end():]
        path = _REF_PATH_RE.search(tail)
        if path:
            targets.append(path.group(0))
            continue
        number = _REF_NUMBER_RE.search(tail)
        if number:
            targets.append(number.group(1))
    return targets


def _record_matches_target(name: str, title: str, target: str) -> bool:
    """Whether *target* (a number or ``*.md`` citation) names this record."""
    if target.lower().endswith(".md"):
        cited = target.split("/")[-1].lower()
        return cited == name.lower()
    target_num = target.lstrip("0") or "0"
    stem = name[:-3] if name.lower().endswith(".md") else name
    stem_num = re.match(r"^.*?(\d+)", stem)
    if stem_num and stem_num.group(1).lstrip("0") == target_num:
        return True
    title_num = _ADR_PREFIX_RE.match(title)
    if title_num:
        digits = re.search(r"\d+", title_num.group(0))
        if digits and digits.group(0).lstrip("0") == target_num:
            return True
    return False


def _superseded_records(records: Dict[str, str]) -> set:
    """The paths of records the batch agrees are no longer in force.

    A record is superseded when its own title or status says it is, or when
    another record in the same scan declares that it supersedes it. Either
    statement is enough to know; nothing else about a record's state is
    read or judged.
    """
    scopes = {path: _supersession_scope(text) for path, text in records.items()}
    raw_titles: Dict[str, str] = {}
    for path, text in records.items():
        heading = _HEADING_RE.match(_title_line(text))
        raw_titles[path] = heading.group(2).strip() if heading else ""
    superseded = {path for path, scope in scopes.items() if _is_superseded(scope)}
    for path, scope in scopes.items():
        for target in _supersedes_targets(scope):
            for other in records:
                if other == path:
                    continue
                name = other.rsplit("/", 1)[-1]
                if _record_matches_target(name, raw_titles[other], target):
                    superseded.add(other)
    return superseded


def _proposals_from_text(
    text: str, record_path: str
) -> List[CriterionProposal]:
    """Propose the rules one record's text states, each citing that record."""
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

    A record whose decision has been superseded — by its own title or status,
    or by the status of the record that replaced it — states no rule in force,
    and nothing is proposed from it. Every other record is proposed from
    exactly as before; no other part of a record's state is read.
    """
    root = Path(project_root)
    if decision_paths is None:
        decision_paths = _detect_decision_paths(root)

    found: Dict[str, tuple[str, str]] = {}
    for rel_dir in decision_paths:
        directory = root / rel_dir
        if not directory.is_dir():
            continue
        for record in sorted(directory.glob("*.md")):
            record_rel = f"{rel_dir}/{record.name}"
            resolved = _resolve_citation(root, rel_dir, record_rel)
            if resolved is None:
                continue
            found[record_rel] = (
                resolved,
                (root / record_rel).read_text(errors="replace"),
            )

    superseded = _superseded_records({rel: pair[1] for rel, pair in found.items()})
    proposals: List[CriterionProposal] = []
    for record_rel, (resolved, text) in found.items():
        if record_rel in superseded:
            continue
        proposals.extend(_proposals_from_text(text, resolved))
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

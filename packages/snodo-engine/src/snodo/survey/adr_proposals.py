"""Parse and verify repository-grounded decision-record proposals.

Model output is untrusted: a proposal is only constructed after each cited
file and any cited line range have been checked against the repository.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

_ADR_PREFIX = re.compile(r"^(?:ADR\s*)?\d+\s*(?:[—–:-]\s*)?", re.IGNORECASE)
_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)
_JSON_ARRAY = re.compile(r"\[.*\]", re.DOTALL)


@dataclass(frozen=True)
class EvidenceReference:
    """Repository file cited in support of a proposal."""

    path: str
    line_range: tuple[int, int] | None = None


@dataclass(frozen=True)
class ADRProposal:
    """A proposed decision with citations that were verified on disk."""

    title: str
    context: str
    decision: str
    consequences: str
    evidence: tuple[EvidenceReference, ...]


def _normalized_title(title: str) -> str:
    return re.sub(r"\s+", " ", _ADR_PREFIX.sub("", title.strip(), count=1)).casefold()


def _decode(raw: str) -> Any:
    """Decode a JSON response, including fenced or prose-wrapped JSON."""
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        pass
    candidates = [*_JSON_ARRAY.findall(raw), *_JSON_OBJECT.findall(raw)]
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    raise ValueError("response did not contain valid JSON")


def _line_range(item: dict[str, Any]) -> tuple[int, int] | None:
    value = item.get("line_range")
    if value is None:
        return None
    if isinstance(value, (list, tuple)) and len(value) == 2:
        start, end = value
    elif isinstance(value, dict):
        start, end = value.get("start"), value.get("end")
    else:
        raise ValueError("line_range must have a start and end")
    if (not isinstance(start, int) or isinstance(start, bool)
            or not isinstance(end, int) or isinstance(end, bool)
            or start < 1 or end < start):
        raise ValueError("line_range must be a valid positive range")
    return start, end


def parse_adr_proposals(
    raw_answer: str,
    project_root: str | Path,
    existing_titles: Sequence[str] = (),
    limit: int = 10,
) -> tuple[list[ADRProposal], list[str]]:
    """Accept grounded proposals and return short reasons for dropped items.

    The response may be a JSON array or an object with a ``proposals`` array.
    Accepted proposals preserve model order and are capped at *limit*.
    """
    try:
        decoded = _decode(raw_answer)
    except (ValueError, TypeError):
        return [], ["Malformed JSON response."]

    items = decoded.get("proposals") if isinstance(decoded, dict) else decoded
    if not isinstance(items, list):
        return [], ["JSON must contain a proposal list."]

    root = Path(project_root).resolve()
    duplicate_titles = {_normalized_title(title) for title in existing_titles}
    accepted: list[ADRProposal] = []
    dropped: list[str] = []
    cap = max(0, limit)
    for index, item in enumerate(items, 1):
        label = f"Proposal {index}"
        if not isinstance(item, dict):
            dropped.append(f"{label}: proposal must be an object.")
            continue
        fields = ("title", "context", "decision", "consequences")
        if any(not isinstance(item.get(field), str) or not item[field].strip() for field in fields):
            dropped.append(f"{label}: title, context, decision, and consequences must be non-empty.")
            continue
        if _normalized_title(item["title"]) in duplicate_titles:
            dropped.append(f"{label}: title duplicates an existing decision record.")
            continue
        citations = item.get("evidence")
        if not isinstance(citations, list):
            dropped.append(f"{label}: evidence must be a list of repository files.")
            continue
        refs: list[EvidenceReference] = []
        invalid: str | None = None
        for citation in citations:
            if not isinstance(citation, dict) or not isinstance(citation.get("path"), str):
                invalid = "evidence reference must have a file path"
                break
            raw_path = citation["path"].strip()
            path = Path(raw_path)
            if not raw_path or path.is_absolute() or ".." in path.parts:
                invalid = f"evidence path {raw_path!r} is not a repository file"
                break
            target = (root / path).resolve()
            if not target.is_relative_to(root) or not target.is_file():
                invalid = f"evidence file {raw_path!r} does not exist in the repository"
                break
            try:
                line_range = _line_range(citation)
            except ValueError as exc:
                invalid = str(exc)
                break
            if line_range is not None:
                line_count = len(target.read_text(errors="replace").splitlines())
                if line_range[1] > line_count:
                    invalid = f"line range for {raw_path!r} is outside the file"
                    break
            refs.append(EvidenceReference(path.as_posix(), line_range))
        if invalid:
            dropped.append(f"{label}: {invalid}.")
            continue
        if len({ref.path for ref in refs}) < 3:
            dropped.append(f"{label}: evidence must cite at least three different files.")
            continue
        if len(accepted) >= cap:
            continue
        accepted.append(ADRProposal(*(item[field].strip() for field in fields), tuple(refs)))
    return accepted, dropped

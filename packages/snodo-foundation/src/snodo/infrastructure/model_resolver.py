"""Pure model resolver — query → Exact / Ambiguous / NotFound.

FILE: snodo/infrastructure/model_resolver.py

Maps user-facing queries ("sonnet", "gpt4o", "gemini") against
discovered ModelInfo candidates.  Pure logic — no I/O, no HTTP.
"""

from typing import List, Literal, Optional

from pydantic import BaseModel

from snodo.infrastructure.model_discovery import ModelInfo


class Resolution(BaseModel):
    """Result of resolving a model query against discovered candidates."""
    status: Literal["exact", "ambiguous", "not_found"]
    match: Optional[ModelInfo] = None
    candidates: List[ModelInfo] = []
    query: str = ""


def _normalize(s: str) -> str:
    """Lowercase and strip hyphens, dots, underscores, slashes for matching."""
    table = str.maketrans("", "", "-_./")
    return s.lower().translate(table)


def resolve_model(query: str, candidates: List[ModelInfo]) -> Resolution:
    """Match *query* against *candidates* by normalized substring.

    The query is normalized (lowercased, stripped of - _ . /) and checked
    as a substring against both the normalized bare model name (last
    segment of ``full_string``) and the normalized ``full_string``.

    Args:
        query: User-facing query string (e.g. "sonnet", "gpt4o", "gemini")
        candidates: Discovered ModelInfo objects to match against

    Returns:
        Resolution with status exact/ambiguous/not_found.
    """
    if not candidates:
        return Resolution(status="not_found", query=query)

    nq = _normalize(query)
    if not nq:
        return Resolution(status="not_found", query=query)

    matches: List[ModelInfo] = []
    for mi in candidates:
        nfull = _normalize(mi.full_string)
        bare = mi.full_string.rsplit("/", 1)[-1]
        nbare = _normalize(bare)
        if nq in nfull or nq in nbare:
            matches.append(mi)

    if len(matches) == 1:
        return Resolution(status="exact", match=matches[0], candidates=matches, query=query)
    elif len(matches) > 1:
        return Resolution(status="ambiguous", candidates=matches, query=query)
    else:
        return Resolution(status="not_found", query=query)

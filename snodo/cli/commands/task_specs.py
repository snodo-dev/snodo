"""Small helpers for rendering task specifications."""

from typing import Optional


def superseded_specs(failure_entry) -> list:
    """Return specs discarded by a replacing retry, oldest first."""
    if not isinstance(failure_entry, dict):
        return []
    history = failure_entry.get("superseded_specs")
    specs = [s for s in (history or []) if isinstance(s, str) and s.strip()]
    if specs:
        return specs
    single = failure_entry.get("superseded_spec")
    return [single] if isinstance(single, str) and single.strip() else []


def spec_excerpt(spec: Optional[str], max_chars: int = 80) -> str:
    """Return a one-line excerpt of a spec for compact task listings."""
    if spec is None:
        return "(unrecoverable description)"
    if not spec:
        return "(empty)"
    one_line = " ".join(spec.split())
    return one_line if len(one_line) <= max_chars else one_line[: max_chars - 1] + "…"

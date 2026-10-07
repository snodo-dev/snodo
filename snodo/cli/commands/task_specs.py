"""Small helpers for rendering task specifications."""

from typing import Optional

_SPEC_DISPLAY_LIMIT = 400


def print_spec_block(spec: str, *, task_id: str, prefix: str = "  ") -> None:
    """Print one specification, truncating long text consistently."""
    if len(spec) > _SPEC_DISPLAY_LIMIT:
        print(f"{prefix}{spec[:_SPEC_DISPLAY_LIMIT]}…")
        print(f"{prefix}(truncated — full spec: snodo task show {task_id} --json)")
    else:
        print(f"{prefix}{spec}")


def unwrap_spec(spec: str) -> str:
    """Unwrap engine scaffolding and retry wrappers around the original request."""
    if not spec:
        return ""
    intent_marker = "INTENT (unchanged from the original task):"
    if intent_marker in spec:
        after_marker = spec.split(intent_marker, 1)[1]
        for marker in ("\n\nCONSTRAINTS:", "\nCONSTRAINTS:"):
            if marker in after_marker:
                after_marker = after_marker.split(marker, 1)[0]
        extracted = after_marker.strip()
        if extracted:
            return unwrap_spec(extracted)
    if "Revised spec (replaces original):" in spec:
        after_revised = spec.split("Revised spec (replaces original):", 1)[1]
        for marker in ("\n\nPrevious attempt", "\nPrevious attempt", "\n\nFiles changed", "\n\nFix the issues"):
            if marker in after_revised:
                after_revised = after_revised.split(marker, 1)[0]
        extracted = after_revised.strip()
        if extracted:
            return unwrap_spec(extracted)
    if spec.startswith("Original spec:"):
        after_orig = spec[len("Original spec:"):].strip()
        for marker in (
            "\n\nPrevious attempt", "\nPrevious attempt", "\n\nRevised spec",
            "\n\nAdded guidance", "\n\nFiles changed", "\n\nFix the issues",
        ):
            if marker in after_orig:
                after_orig = after_orig.split(marker, 1)[0]
        extracted = after_orig.strip()
        if extracted:
            return unwrap_spec(extracted)
    return spec.strip()


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

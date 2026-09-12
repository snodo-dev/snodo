"""Text-level helpers for task specifications.

A specification is the unit of authority in a run: the coder builds against it
and the validators judge against it. The retry path is the one place an
operator can change it by hand, and changing it by accident is unrecoverable —
a command snodo printed as advice once replaced a sixty-line spec with two
words. These helpers keep the three things the retry path can do to a spec
(bare, annotated, replaced) distinguishable in code as well as at the CLI, so
no layer has to re-derive "does this text change the specification?" and get it
subtly wrong.
"""

from typing import Optional


def spec_text(value: Optional[str]) -> str:
    """Return *value* stripped, or "" when it is missing or blank.

    Blank and absent are the same intent — a retry given ``--append-spec ""``
    is a retry given nothing.
    """
    if isinstance(value, str) and value.strip():
        return value.strip()
    return ""


def same_spec(a: Optional[str], b: Optional[str]) -> bool:
    """Whether two specs say the same thing, ignoring whitespace layout."""
    return " ".join((a or "").split()) == " ".join((b or "").split())


def spec_with_guidance(spec: Optional[str], guidance: Optional[str]) -> str:
    """Return *spec* with *guidance* joined onto it.

    The additive form of a retry: what was written still stands and the new
    text is appended, so the original is never the thing at risk.
    """
    spec = spec_text(spec)
    guidance = spec_text(guidance)
    if not spec:
        return guidance
    if not guidance:
        return spec
    return f"{spec}\n\n{guidance}"

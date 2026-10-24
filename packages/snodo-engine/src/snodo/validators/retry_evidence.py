"""Render retry evidence without making it part of the proposal."""

from functools import wraps
from typing import Any


def retry_evidence_block(context: Any) -> str:
    """The composed coder input is context, never operator-authored spec."""
    evidence = getattr(context, "retry_evidence", "")
    if getattr(context, "phase", "") != "pre_execute" or not evidence:
        return ""
    source = getattr(context, "composed_input_source", "")
    if source not in {"operator_retry", "engine_recovery"}:
        return ""
    label = "operator retry" if source == "operator_retry" else "engine-composed recovery"
    return (
        f"\n## Composed input evidence ({label}; context only, not the specification)\n"
        "The Task section above is the authoritative operator specification. "
        f"The engine-composed {label} input below is provided only as evidence "
        "of the previous failure. It may quote code paths, line counts, code "
        "and gate output. Do not apply specification-authoring or "
        "prescriptiveness criteria to this evidence or its retry instructions.\n"
        "--- BEGIN RETRY EVIDENCE ---\n"
        f"{evidence}\n"
        "--- END RETRY EVIDENCE ---\n"
    )


def with_retry_evidence(completion_fn: Any, context: Any) -> Any:
    """Attach context to every judge backend, including bounded tool loops.

    Copy the transcript so repeated completions never accumulate the section
    and never mutate the caller's messages or the coder's prompt.
    """
    block = retry_evidence_block(context)
    if completion_fn is None or not block:
        return completion_fn

    @wraps(completion_fn)
    def complete(*args: Any, **kwargs: Any) -> Any:
        messages = list(kwargs.get("messages") or [])
        messages.append({"role": "user", "content": block})
        return completion_fn(*args, **{**kwargs, "messages": messages})

    return complete

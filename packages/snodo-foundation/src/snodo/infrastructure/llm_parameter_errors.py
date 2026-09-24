"""Shared detection and removal for provider-rejected LLM parameters."""

import re
from typing import Any, Optional


_PARAMETER_REJECTION_PATTERNS = (
    r"(?:unsupported|unrecognized|unknown|invalid)\s+(?:request\s+)?"
    r"(?:parameter|param|argument)\s*[:=]?\s*[`'\"]?"
    r"([A-Za-z_][A-Za-z0-9_.-]*)",
    r"[`'\"]([A-Za-z_][A-Za-z0-9_.-]*)[`'\"]?\s+"
    r"(?:is\s+)?(?:not\s+supported|unsupported|invalid)",
    r"[`'\"]([A-Za-z_][A-Za-z0-9_.-]*)[`'\"]\s+does\s+not\s+support\b",
    r"\b([A-Za-z_][A-Za-z0-9_.-]*)\b\s+is\s+not\s+supported",
)


def rejected_parameter_name(error: Exception) -> Optional[str]:
    """Extract a named parameter from provider rejection wording."""
    message = str(error)
    for pattern in _PARAMETER_REJECTION_PATTERNS:
        match = re.search(pattern, message, re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def is_provider_request_rejection(error: Exception) -> bool:
    """Return whether an error is a provider-side non-transient request rejection."""
    try:
        from litellm.exceptions import (
            BadRequestError,
            InvalidRequestError,
            UnsupportedParamsError,
        )
        if isinstance(error, (BadRequestError, InvalidRequestError, UnsupportedParamsError)):
            return True
    except ImportError:
        pass
    status = getattr(error, "status_code", None)
    return isinstance(status, int) and 400 <= status < 500 and status != 429


def remove_rejected_parameter(
    error: Exception,
    kwargs: dict[str, Any],
    removed: set[str],
) -> Optional[str]:
    """Remove one named provider-rejected kwarg at most once per caller run."""
    if not is_provider_request_rejection(error):
        return None
    parameter = rejected_parameter_name(error)
    if parameter and parameter in kwargs and parameter not in removed:
        removed.add(parameter)
        del kwargs[parameter]
        return parameter
    return None

"""Shared adaptation for provider-rejected LLM parameters."""

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
_REQUESTED_VALUE_RE = re.compile(
    r"\bset\s+([A-Za-z_][A-Za-z0-9_.-]*)\s+to\s+(['\"])(.*?)\2",
    re.IGNORECASE,
)
_ALLOWED_REQUESTED_VALUES = {"reasoning_effort": {"none"}}
_MODEL_PARAMETER_VALUES: dict[str, dict[str, str]] = {}


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


def apply_remembered_parameters(model: str, kwargs: dict[str, Any]) -> None:
    """Apply parameter values previously requested by this model."""
    kwargs.update(_MODEL_PARAMETER_VALUES.get(model, {}))


def remove_rejected_parameter(
    error: Exception,
    kwargs: dict[str, Any],
    removed: set[str],
    model: Optional[str] = None,
) -> Optional[str]:
    """Adapt one rejected parameter at most once per run, by setting/removing it."""
    if model:
        apply_remembered_parameters(model, kwargs)
    if not is_provider_request_rejection(error):
        return None
    value_match = _REQUESTED_VALUE_RE.search(str(error))
    if value_match:
        parameter, value = value_match.group(1), value_match.group(3)
        if (
            parameter in _ALLOWED_REQUESTED_VALUES
            and value in _ALLOWED_REQUESTED_VALUES[parameter]
            and parameter not in removed
        ):
            removed.add(parameter)
            kwargs[parameter] = value
            if model:
                _MODEL_PARAMETER_VALUES.setdefault(model, {})[parameter] = value
            return parameter
    parameter = rejected_parameter_name(error)
    if parameter and parameter in kwargs and parameter not in removed:
        removed.add(parameter)
        del kwargs[parameter]
        return parameter
    return None

"""Provider error classification and response helpers for the LLM judge loop.

Split out of ``llm_validator.py`` (Fixes #360): these are pure functions with
no dependency on ``LLMValidator`` itself — classifying an exception or a
model name, or reading token usage off a litellm response — and keeping them
alongside the judge's turn-loop logic was what pushed that file over the
repo's file-length limit. Nothing here changed in the move.
"""

import logging
import re
from typing import Optional

# ``usage_tokens_of`` lives with the response-field readers in
# snodo.infrastructure.usage_tracker; the name stays importable from here for
# the judge loop (Fixes #381 moved it, nothing else).
from snodo.infrastructure.usage_tracker import usage_tokens_of as _usage_tokens  # noqa: F401 - re-export for the judge loop

_logger = logging.getLogger(__name__)


def _is_gemini3_plus(model: str) -> bool:
    m = re.search(r'gemini-(\d+)', model)
    return bool(m and int(m.group(1)) >= 3)


def _is_transient_error(e: Exception) -> bool:
    """Return True if *e* is a transient provider/network error worth retrying.

    Classifies on exception type and HTTP status code, not on error prose.
    The previous predicate substring-matched the message against terms like
    ``"500"``, ``"502"`` and ``"deepseekexception"``, so every error from a
    provider whose name contained those letters was retryable, and a bare
    status code matched those digits anywhere in the text. A 4xx (except 429)
    is a client error — retrying it is not honest; a 5xx, 429, connection or
    timeout is transient.
    """
    # Network-level builtins: genuinely transient.
    if isinstance(e, (ConnectionError, TimeoutError)):
        return True
    # DNS resolution failure (errno 8: nodename nor servname).
    if isinstance(e, OSError) and getattr(e, "errno", None) == 8:
        return True

    # litellm exception classes.
    try:
        from litellm.exceptions import (
            APIConnectionError,
            Timeout as LiteLLMTimeout,
            RateLimitError,
            InternalServerError,
            BadGatewayError,
            ServiceUnavailableError,
        )
        if isinstance(
            e,
            (APIConnectionError, LiteLLMTimeout, RateLimitError,
             InternalServerError, BadGatewayError, ServiceUnavailableError),
        ):
            return True
    except ImportError as e:
        # The classifier's own degradation must be visible: without these
        # classes a transient connection error is judged only by status code,
        # and a caller that carries on should be able to see why the
        # classification changed shape.
        _logger.debug(
            "litellm exception classes unavailable, transient check falls "
            "back to status code: %s", e,
        )

    # Fall back to the HTTP status code when the exception carries one.
    status = getattr(e, "status_code", None)
    if isinstance(status, int):
        return status in (429, 500, 502, 503, 504)
    return False


def _is_provider_rejection(e: Exception) -> bool:
    """Return True if *e* is a provider rejecting the request (a 4xx client error).

    Used to distinguish "the provider refused response_format" (a 400 like
    DeepSeek's "This response_format type is unavailable now") or forced
    tool_choice from "the model returned garbage" — only the former makes an
    unparseable fallback an operational fault rather than a warn verdict (Fixes #84, #296).
    """
    try:
        from litellm.exceptions import (
            BadRequestError,
            InvalidRequestError,
            UnsupportedParamsError,
        )
        if isinstance(e, (BadRequestError, InvalidRequestError, UnsupportedParamsError)):
            return True
    except ImportError:
        pass
    status = getattr(e, "status_code", None)
    if isinstance(status, int):
        return 400 <= status < 500 and status != 429
    return False


def _provider_rejected_parameter(e: Exception) -> Optional[str]:
    """Extract a parameter name from a provider's named-parameter rejection."""
    if not _is_provider_rejection(e):
        return None
    message = str(e)
    patterns = (
        r"(?:unsupported|unrecognized|unknown|invalid)\s+(?:request\s+)?"
        r"(?:parameter|param|argument)\s*[:=]?\s*[`'\"]?"
        r"([A-Za-z_][A-Za-z0-9_.-]*)",
        r"[`'\"]([A-Za-z_][A-Za-z0-9_.-]*)[`'\"]?\s+"
        r"(?:is\s+)?(?:not\s+supported|unsupported|invalid)",
        r"\b([A-Za-z_][A-Za-z0-9_.-]*)\b\s+is\s+not\s+supported",
    )
    for pattern in patterns:
        match = re.search(pattern, message, re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def _provider_retry_delay(e: Exception) -> Optional[float]:
    """Return a provider-supplied retry delay, if the exception carries one."""
    candidates = [getattr(e, "retry_after", None)]
    headers = getattr(e, "headers", None)
    response = getattr(e, "response", None)
    response_headers = getattr(response, "headers", None)
    for header_map in (headers, response_headers):
        if header_map is not None:
            candidates.append(header_map.get("retry-after"))
            candidates.append(header_map.get("Retry-After"))

    for value in candidates:
        try:
            delay = float(value)
        except (TypeError, ValueError):
            continue
        if delay >= 0:
            return delay
    return None

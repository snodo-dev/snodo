"""Fallback message shaping for strict OpenAI-compatible providers."""

from typing import Any

from snodo.infrastructure.llm_parameter_errors import is_provider_request_rejection

_ADAPTED_MODELS: set[str] = set()


def is_message_shape_rejection(error: Exception) -> bool:
    """Recognize request rejections that identify message-content field shape."""
    if not is_provider_request_rejection(error):
        return False
    text = str(error).lower()
    return ("message" in text and "content" in text and "array" in text) or (
        "required properties" in text and "role" in text and "content" in text
    )


def message_shape_adapted_models() -> frozenset[str]:
    """Return models remembered as requiring plain string message content."""
    return frozenset(_ADAPTED_MODELS)


def reshape_messages_after_rejection(
    error: Exception,
    messages: list[dict[str, Any]],
    model: str,
    attempted_models: set[str],
) -> list[dict[str, Any]] | None:
    """Reshape messages after a matching rejection, once per run and model."""
    if model in attempted_models or not is_message_shape_rejection(error):
        return None
    attempted_models.add(model)
    _ADAPTED_MODELS.add(model)
    reshaped: list[dict[str, Any]] = []
    for message in messages:
        copy = dict(message)
        content = copy.get("content")
        if isinstance(content, list) and all(
            isinstance(part, dict) and part.get("type") == "text"
            and isinstance(part.get("text"), str)
            for part in content
        ):
            copy["content"] = "".join(part["text"] for part in content)
        elif content is None and copy.get("role") == "assistant" and copy.get("tool_calls"):
            copy["content"] = ""
        reshaped.append(copy)
    return reshaped

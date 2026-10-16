"""Provider-request adaptation for recon completion calls."""

from collections.abc import Callable
from typing import Any

from snodo.infrastructure.llm_parameter_errors import remove_rejected_parameter
from snodo.infrastructure.message_shape import reshape_messages_after_rejection


def complete_with_adaptation(
    completion: Callable[..., Any],
    kwargs: dict[str, Any],
    model: str,
    attempted_shape_models: set[str],
) -> Any:
    """Retry a recon request after shared parameter or message-shape fixes."""
    removed: set[str] = set()
    while True:
        try:
            return completion(**kwargs)
        except Exception as error:
            parameter = remove_rejected_parameter(error, kwargs, removed, model)
            if parameter:
                continue
            reshaped = reshape_messages_after_rejection(
                error, kwargs["messages"], model, attempted_shape_models,
            )
            if reshaped is None:
                raise
            kwargs["messages"] = reshaped

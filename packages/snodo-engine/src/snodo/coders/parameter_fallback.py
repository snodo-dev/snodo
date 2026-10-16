"""Retry LiteLLM calls after a provider rejects an explicit request parameter."""

from typing import Any, Callable

from snodo.infrastructure.llm_parameter_errors import remove_rejected_parameter
from snodo.infrastructure.message_shape import reshape_messages_after_rejection


def completion_without_rejected_parameters(
    completion_fn: Callable[..., Any],
    kwargs: dict[str, Any],
    removed: set[str],
    logger: Any,
    model: str,
) -> Any:
    """Retry named provider rejections and remember removed kwargs per run."""
    kwargs = {key: value for key, value in kwargs.items() if key not in removed}
    shaped: set[str] = set()
    while True:
        try:
            return completion_fn(**kwargs)
        except Exception as error:
            reshaped = reshape_messages_after_rejection(
                error, kwargs.get("messages", []), model, shaped,
            )
            if reshaped is not None:
                kwargs["messages"] = reshaped
                continue
            parameter = remove_rejected_parameter(error, kwargs, removed, model)
            if parameter is None:
                raise
            logger.warning(
                "Coder provider rejected parameter %s (model=%s): %s; retrying without it",
                parameter, model, error,
            )

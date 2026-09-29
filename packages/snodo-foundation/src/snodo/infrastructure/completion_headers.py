"""Task-scoped provider headers for LiteLLM completion calls."""

from typing import Any


def wrap_completion_fn_with_headers(completion_fn: Any, task_id: str) -> Any:
    """Inject configured provider headers using the configured model and task.

    ``_configured_model`` is consumed here so callers retain their bound
    LiteLLM routing model and credentials while header resolution sees the
    original configured provider name.
    """
    from snodo.config import ConfigManager

    def headers_aware_wrapper(**kwargs: Any) -> Any:
        resolution_model = kwargs.pop("_configured_model", None) or kwargs.get("model")
        if resolution_model and "extra_headers" not in kwargs:
            extra_headers = ConfigManager.resolve_extra_headers(
                resolution_model, task_id=task_id
            )
            if extra_headers:
                kwargs["extra_headers"] = extra_headers
        return completion_fn(**kwargs)

    return headers_aware_wrapper

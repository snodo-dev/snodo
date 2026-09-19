"""Configured model canary checks."""

from typing import Any, Callable, Optional


def run_canary_call(model: str, completion_fn: Optional[Any] = None) -> None:
    """Make the smallest request that exercises snodo's constrained LLM path."""
    import litellm
    from snodo.config import ConfigManager
    from snodo.validators.llm_provider_errors import _remove_rejected_parameter

    if completion_fn is None:
        completion_fn = litellm.completion

    kwargs = {
        "model": ConfigManager.resolve_litellm_model(model),
        "messages": [{"role": "user", "content": "Reply with OK."}],
        "max_tokens": 1,
        "temperature": 0.0,
        "tools": [{
            "type": "function",
            "function": {
                "name": "submit_verdict",
                "description": "Return the result.",
                "parameters": {
                    "type": "object",
                    "properties": {"result": {"type": "string"}},
                    "required": ["result"],
                },
            },
        }],
        "tool_choice": {
            "type": "function",
            "function": {"name": "submit_verdict"},
        },
    }
    api_key = ConfigManager().get_key_for_model(model)
    if api_key:
        kwargs["api_key"] = api_key
    api_base = ConfigManager.resolve_api_base(model)
    if api_base:
        kwargs["api_base"] = api_base
    extra_headers = ConfigManager.resolve_extra_headers(model, task_id="model-check")
    if extra_headers:
        kwargs["extra_headers"] = extra_headers
    removed = set()
    while True:
        try:
            completion_fn(**kwargs)
            return
        except Exception as error:
            if _remove_rejected_parameter(error, kwargs, removed) is None:
                raise


def _subprocess_coder_for_model(model: str) -> Optional[str]:
    """Return the subprocess adapter named by *model*, if it has one."""
    from snodo.coders import CODER_REGISTRY
    from snodo.coders.subprocess_adapter import SubprocessCoderAdapter

    for coder_name, adapter_cls in CODER_REGISTRY.items():
        if issubclass(adapter_cls, SubprocessCoderAdapter) and any(
            model.startswith(prefix) for prefix in adapter_cls.model_prefixes()
        ):
            return coder_name
    return None


def check_configured_models(
    configured_models: Callable[[], list[tuple[str, str]]],
    canary_call: Callable[[str], None],
) -> int:
    """Check provider models and classify subprocess models honestly."""
    from snodo.coders.availability import check_coder_available

    results = []
    for role, model in configured_models():
        coder_name = _subprocess_coder_for_model(model)
        if coder_name is not None:
            missing = check_coder_available(coder_name)
            if missing:
                binary, remediation = missing
                results.append((
                    role, model, False,
                    f"{coder_name} binary {binary!r} is unavailable: {remediation}",
                ))
            else:
                results.append((
                    role, model, None,
                    f"{coder_name} uses a subprocess; provider canary not applicable",
                ))
            continue
        try:
            canary_call(model)
        except Exception as error:
            results.append((role, model, False, str(error)))
        else:
            results.append((role, model, True, ""))

    if not results:
        print("No models configured.")
        return 0

    print("Configured model check:")
    for role, model, healthy, reason in results:
        if healthy:
            print(f"  OK       {model} ({role})")
        elif healthy is None:
            print(f"  NOT CHECKABLE {model} ({role}): {reason}")
        else:
            print(f"  FAILED   {model} ({role}): {reason}")
    return 0 if all(healthy is not False for _, _, healthy, _ in results) else 1

"""Configured model canary checks."""

import logging
from typing import Any, Callable, Optional

_logger = logging.getLogger(__name__)


def run_canary_call(model: str, completion_fn: Optional[Any] = None, role: str = "validator") -> None:
    """Exercise the request shape the configured role actually uses."""
    import litellm
    from snodo.config import ConfigManager
    from snodo.validators.llm_provider_errors import (
        _is_gemini3_plus, _remove_rejected_parameter, call_with_unforced_tool_fallback,
    )

    if completion_fn is None:
        completion_fn = litellm.completion

    from snodo.infrastructure.config import load_llm_config
    llm_config = load_llm_config()
    kwargs = {
        "model": ConfigManager.resolve_litellm_model(model),
        "messages": [{"role": "user", "content": "Reply with OK."}],
    }
    if role == "validator":
        from snodo.validators.llm_validator import LLMValidator, _UNFORCED_VERDICT_INSTRUCTION
        kwargs["messages"] = [{"role": "user", "content": "Call submit_verdict with severity pass and justification OK."}]
        kwargs["tools"] = [LLMValidator._SUBMIT_VERDICT_DEF]
        kwargs["tool_choice"] = {"type": "function", "function": {"name": "submit_verdict"}}
        kwargs["max_tokens"] = llm_config.validator.max_tokens
    elif role == "recon":
        from snodo.recon import _READ_ONLY_TOOLS
        kwargs["tools"] = _READ_ONLY_TOOLS
        # Recon does not set max_tokens, temperature, or tool_choice.
    elif role == "classifier":
        classifier = llm_config.classifier
        kwargs["max_tokens"] = classifier.max_tokens
        kwargs["temperature"] = classifier.temperature
        try:
            if litellm.supports_response_format(model, {"type": "json_object"}):
                kwargs["response_format"] = {"type": "json_object"}
        except Exception as error:
            _logger.debug("Classifier response_format support probe unavailable: %s", error)
        kwargs["messages"] = [{"role": "user", "content": 'Return {"flow_type":"feature","wave_id":"new","task_summary":"OK","feature_description":"OK"} as JSON.'}]
    else:
        # The LiteLLM coder offers tools, but never requires a specific call.
        from snodo.recon import _READ_ONLY_TOOLS
        kwargs["tools"] = _READ_ONLY_TOOLS
        kwargs["parallel_tool_calls"] = True
        kwargs["max_tokens"] = llm_config.coder.max_tokens
    if role in ("validator", "coder") and not _is_gemini3_plus(model):
        kwargs["temperature"] = 0.0
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

    def call(**request):
        while True:
            try:
                return completion_fn(**request)
            except Exception as error:
                parameter = _remove_rejected_parameter(error, request, removed)
                if parameter is None:
                    raise
                if role == "validator" and parameter == "tool_choice":
                    request["messages"].append({"role": "user", "content": _UNFORCED_VERDICT_INSTRUCTION})

    if role == "validator":
        call_with_unforced_tool_fallback(call, kwargs, instruction=_UNFORCED_VERDICT_INSTRUCTION)
    else:
        call(**kwargs)


def _check_opencode_cli_model(model: str) -> str:
    """Best-effort lookup in snodo's catalog; OpenCode owns its own names."""
    import os
    from snodo.config import ConfigManager
    from snodo.cli.commands.models_cmd import _get_models

    inner = model.removeprefix("opencode-cli/")
    provider, separator, model_id = inner.partition("/")
    verify = "Verify in OpenCode with `opencode models`."
    providers = ConfigManager().get_providers()
    pc = providers.get(provider)
    if not separator or not model_id:
        return f"No provider/model pair to look up; {verify}"
    if pc is None or not (pc.api_key or pc.api_key_ref or (pc.api_key_env and os.environ.get(pc.api_key_env))):
        return f"Provider '{provider}' is not configured in snodo; {verify}"
    try:
        models = _get_models(provider, pc, force_refresh=True)
    except Exception:
        return f"Could not query snodo provider '{provider}' (best-effort); {verify}"
    found = any(item.get("id") == model_id or item.get("full_string") == inner for item in models)
    outcome = "FOUND" if found else "NOT FOUND"
    return f"{outcome} in snodo's {provider} model list (best-effort; provider names may differ); {verify}"


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
    canary_call: Callable[..., None],
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
                if coder_name == "opencode-cli":
                    results.append((role, model, None, _check_opencode_cli_model(model)))
                    continue
                results.append((
                    role, model, None,
                    f"{coder_name} uses a subprocess; provider canary not applicable",
                ))
            continue
        try:
            canary_call(model, role=role)
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

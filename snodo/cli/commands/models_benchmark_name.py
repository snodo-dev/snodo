"""Parse the provider/model shorthand accepted by ``models --benchmark``."""


def resolve_benchmark_name(args):
    """Return provider and model filters, or raise a user-facing validation error."""
    provider = getattr(args, "provider", None)
    model_id = getattr(args, "id", None)
    named_model = getattr(args, "benchmark_model", None)
    if named_model is None:
        return provider, model_id
    if provider is not None or model_id is not None:
        raise ValueError("Use --benchmark <provider>/<model> or --provider and --id, not both.")
    if not named_model or "/" not in named_model:
        raise ValueError("--benchmark model must be a non-empty <provider>/<model> value.")
    provider, model_id = named_model.split("/", 1)
    if not provider or not model_id:
        raise ValueError("--benchmark model must have non-empty provider and model parts.")
    args.id = model_id
    return provider, model_id


def select_benchmark_model(provider_name, args, get_models=None, apply_filters=None):
    """Resolve exactly one discovered model from benchmark filters."""
    from snodo.cli.commands.models_cmd import _apply_discrete_filters, _get_models
    get_models = get_models or _get_models
    apply_filters = apply_filters or _apply_discrete_filters
    from snodo.config import ConfigManager
    import sys

    providers = ConfigManager().get_providers()
    pc = providers.get(provider_name)
    if not pc:
        print(f"Provider not configured: {provider_name}", file=sys.stderr)
        print(f"  Configured: {', '.join(sorted(providers.keys()))}", file=sys.stderr)
        return None
    models = get_models(provider_name, pc, force_refresh=getattr(args, "flush", False))
    if not models:
        if ConfigManager().provider_uses_oauth(provider_name):
            model_id = getattr(args, "id", None)
            if not model_id:
                print(
                    f"Provider {provider_name} cannot list its models; an exact --id is required.",
                    file=sys.stderr,
                )
                return None
            from snodo.infrastructure.chatgpt_oauth import chatgpt_sign_in_state
            if chatgpt_sign_in_state() is None:
                print(
                    f"ChatGPT sign-in is required; run `snodo models --provider {provider_name} --authenticate`.",
                    file=sys.stderr,
                )
                return None
            return f"{provider_name}/{model_id}"
        print(f"No models discovered for {provider_name}", file=sys.stderr)
        return None
    models = apply_filters(
        models,
        model_id=getattr(args, "id", None),
        id_contains=getattr(args, "id_contains", None),
        max_output_cost=getattr(args, "max_output_cost", None),
        min_output_cost=getattr(args, "min_output_cost", None),
        max_input_cost=getattr(args, "max_input_cost", None),
        min_context=getattr(args, "min_context", None),
    )
    if not models:
        model_id = getattr(args, "id", None)
        if model_id is not None:
            print(f"No model matched --id={model_id}.", file=sys.stderr)
        else:
            print("No models matched the specified filters.", file=sys.stderr)
        return None
    if len(models) > 1:
        print(
            f"--benchmark needs exactly one model, but {len(models)} matched. "
            "Select one with --id=<exact-id> or --id-contains=<substring>:",
            file=sys.stderr,
        )
        for model in models:
            print(f"  {model.get('full_string', model.get('id', ''))}", file=sys.stderr)
        return None
    return models[0].get("full_string") or models[0].get("id")

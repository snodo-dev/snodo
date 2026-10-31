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

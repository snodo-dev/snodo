"""Cost wording for explicit live model benchmarks."""


def benchmark_cost_message(model: str, runs: int) -> str:
    """Describe whether benchmark calls use a subscription or are billed."""
    provider = model.split("/", 1)[0]
    from snodo.config import ConfigManager

    if ConfigManager().provider_uses_oauth(provider):
        return f"These {runs} call(s) use your provider subscription; they are not billed per API call."
    return f"This makes {runs} real, billed API call(s)."

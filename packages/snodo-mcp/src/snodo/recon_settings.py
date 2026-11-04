"""Shared resolution and validation for recon configuration settings."""

def resolve_recon_settings(config):
    """Return configured recon budgets, validating the deadline consistently."""
    from snodo.recon import (
        RECON_DEFAULT_DEADLINE_SECONDS, RECON_DEFAULT_MAX_TOKENS,
        RECON_DEFAULT_MAX_TOOL_TURNS,
    )

    recon = config.get("llm", {}).get("recon", {})
    deadline = recon.get("deadline_seconds", RECON_DEFAULT_DEADLINE_SECONDS)
    if isinstance(deadline, bool):
        raise ValueError("llm.recon.deadline_seconds must be a positive integer")
    try:
        deadline = int(deadline)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("llm.recon.deadline_seconds must be a positive integer") from exc
    if deadline <= 0:
        raise ValueError("llm.recon.deadline_seconds must be a positive integer")
    return {
        "max_tool_turns": recon.get("max_tool_turns", RECON_DEFAULT_MAX_TOOL_TURNS),
        "max_tokens": recon.get("max_tokens", RECON_DEFAULT_MAX_TOKENS),
        "deadline_seconds": deadline,
    }

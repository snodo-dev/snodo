"""Credential checks performed before starting a run."""

import os
from typing import Optional

from snodo.config import ConfigManager


def preflight_provider_credential(model: str) -> Optional[str]:
    """Return an error when the selected provider is not authenticated."""
    mgr = ConfigManager()
    provider = ConfigManager._provider_for_model(model)
    if provider is None:
        return None
    pc = mgr.get_providers().get(provider)
    if mgr.provider_uses_oauth(provider):
        from snodo.infrastructure.chatgpt_oauth import chatgpt_sign_in_state

        if chatgpt_sign_in_state() is not None:
            return None
        return (
            f"Error: Provider '{provider}' is not signed in. Run: "
            f"snodo models --provider {provider} --authenticate"
        )
    env_var = pc.api_key_env if pc else ""
    if mgr.get_key_for_model(model) or (env_var and os.environ.get(env_var)):
        return None
    if not env_var:
        return None
    return (
        f"Error: No credential for provider '{provider}' (model {model}). "
        f"Set {env_var} or run: snodo config add {provider} <key>"
    )

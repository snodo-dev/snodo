"""Run LiteLLM's headless ChatGPT device-code sign-in."""

from datetime import datetime, timezone
import sys
import time


def _error(args, message: str) -> int:
    if getattr(args, "json", False):
        from snodo.cli.json_output import emit_error
        return emit_error("models-authenticate", message, 1)
    print(message, file=sys.stderr)
    return 1


def models_authenticate_command(args) -> int:
    """Authenticate an OAuth provider without disturbing API-key providers."""
    provider = getattr(args, "provider", None)
    if not provider:
        return _error(args, "--authenticate requires --provider=<name>.")
    modes = ("stats", "provenance", "check", "benchmark", "set_baseline",
             "compare", "benchmark_run")
    if any(getattr(args, flag, False) for flag in modes):
        return _error(args, "--authenticate cannot be combined with another models mode.")

    from snodo.config import ConfigManager
    manager = ConfigManager()
    providers = manager.get_providers()
    if provider not in providers:
        return _error(args, f"Provider not configured: {provider}")
    if not manager.provider_uses_oauth(provider):
        return _error(args, f"Provider '{provider}' uses an API key; nothing was changed.")

    from snodo.infrastructure.chatgpt_oauth import (
        chatgpt_sign_in_state, chatgpt_token_lock,
    )
    state = chatgpt_sign_in_state()
    expiry = state.get("expires_at") if state else None
    try:
        valid = bool(state and float(expiry) > time.time() + 300)
    except (TypeError, ValueError):
        valid = False
    if valid and not getattr(args, "reauthenticate", False):
        if not getattr(args, "json", False):
            print(f"Provider '{provider}' is already signed in until {datetime.fromtimestamp(float(expiry)).astimezone().isoformat()}.")
        return 0

    try:
        with chatgpt_token_lock():
            from litellm.llms.chatgpt.authenticator import Authenticator
            Authenticator().get_access_token()
        state = chatgpt_sign_in_state()
        if not state:
            raise RuntimeError("sign-in did not produce a token")
    except KeyboardInterrupt:
        return _error(args, "Sign-in interrupted; no sign-in was completed.")
    except Exception as exc:
        detail = str(exc).lower()
        reason = "denied" if "denied" in detail or "declined" in detail else "expired" if "expir" in detail else "failed"
        return _error(args, f"Sign-in {reason}: {exc}")

    expires = state.get("expires_at")
    try:
        until = datetime.fromtimestamp(float(expires), timezone.utc).astimezone().isoformat()
    except (TypeError, ValueError, OSError):
        until = "an unspecified time"
    if not getattr(args, "json", False):
        print(f"Provider '{provider}' is signed in until {until}.")
    return 0

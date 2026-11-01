"""Provider credential availability and listing for ``snodo models``."""

import os


def _configured_provider_names(providers: dict) -> list[str]:
    """Return configured providers whose credentials are available."""
    from snodo.config import ConfigManager
    return sorted(
        name for name, pc in providers.items()
        if (ConfigManager().provider_uses_oauth(name)
            or (ConfigManager().provider_key_available(name) if name == "snodo-cloud"
                else pc.api_key or (pc.api_key_env and os.environ.get(pc.api_key_env))))
    )


def _list_providers(providers: dict) -> int:
    """Print configured providers and the recovery hint for cloud login."""
    from snodo.config import ConfigManager

    configured = _configured_provider_names(providers)
    if not configured:
        print("No providers configured. Add a key with: snodo config add <provider> <key>")
        print("  Then run: snodo models --provider=<name>")
        if "snodo-cloud" in providers:
            print("  snodo-cloud    unavailable — run: snodo cloud login")
        return 0
    print("Configured providers:\n")
    for name in configured:
        if ConfigManager().provider_uses_oauth(name):
            from snodo.infrastructure.chatgpt_oauth import chatgpt_sign_in_state

            if chatgpt_sign_in_state() is not None:
                status = "signed in"
            else:
                status = f"not signed in — run: snodo models --provider {name} --authenticate"
            print(f"  {name:<14} {status}")
        else:
            print(f"  {name:<14} configured")
    if "snodo-cloud" in providers and "snodo-cloud" not in configured:
        print("  snodo-cloud    unavailable — run: snodo cloud login")
    print("\nRun: snodo models --provider=<name> to list models")
    return 0

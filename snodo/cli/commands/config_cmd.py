"""Config command - Manage Snodo configuration and API keys.

FILE: snodo/cli/commands/config_cmd.py
"""

import sys

from snodo.cli.config import ConfigManager, ConfigError, DEFAULT_MODEL


def config_command(args) -> int:
    """Manage Snodo configuration and API keys."""
    mgr = ConfigManager()

    if args.config_action == "show":
        return _config_show(mgr)
    elif args.config_action == "add":
        return _config_add(mgr, args.provider, args.key)
    elif args.config_action == "remove":
        return _config_remove(mgr, args.provider)
    elif args.config_action == "test":
        return _config_test(mgr)
    elif args.config_action == "set":
        return _config_set(mgr, args.key, args.value)
    elif args.config_action == "get":
        return _config_get(mgr, args.key)
    else:
        print("Unknown config action. Use: show, add, remove, test, set, get", file=sys.stderr)
        return 1


def _config_show(mgr: ConfigManager) -> int:
    """Show current configuration."""
    config = mgr.load()
    keys = config.get("api_keys", {})
    model = config.get("model", DEFAULT_MODEL)

    print(f"Config: {mgr.config_path}")
    print(f"Model:  {model}")
    print()

    if keys:
        print("API Keys:")
        for provider, key in keys.items():
            print(f"  {provider}: {ConfigManager.mask_key(key)}")
    else:
        print("No API keys configured.")
        print("  Add one: snodo config add <provider> <key>")
    return 0


def _config_add(mgr: ConfigManager, provider: str, key: str) -> int:
    """Add an API key."""
    try:
        mgr.add_key(provider, key)
        masked = ConfigManager.mask_key(key)
        print(f"✓ Stored {provider} key: {masked}")
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def _config_remove(mgr: ConfigManager, provider: str) -> int:
    """Remove an API key."""
    if mgr.remove_key(provider):
        print(f"✓ Removed {provider} key")
        return 0
    else:
        print(f"No key found for provider: {provider}", file=sys.stderr)
        return 1


def _config_test(mgr: ConfigManager) -> int:
    """Test all configured API keys."""
    config = mgr.load()
    keys = config.get("api_keys", {})
    if not keys:
        print("No API keys configured. Add one first:")
        print("  snodo config add <provider> <key>")
        return 1

    print("Testing API keys...")
    results = mgr.test_keys()
    all_ok = True
    for provider, ok in results.items():
        status = "✓ valid" if ok else "✗ invalid"
        print(f"  {provider}: {status}")
        if not ok:
            all_ok = False
    return 0 if all_ok else 1


def _config_set(mgr: ConfigManager, key: str, value: str) -> int:
    """Set a config value using dot notation."""
    parts = key.split(".", 1)
    if len(parts) == 2 and parts[0] == "engine":
        engine_key = parts[1]
        if engine_key in ("max_subtask_depth", "max_session_age_days", "token_ttl_seconds"):
            try:
                int_value = int(value)
            except ValueError:
                print(f"Error: {engine_key} must be an integer", file=sys.stderr)
                return 1
            try:
                mgr.set_engine_value(engine_key, int_value)
            except (ValueError, ConfigError) as e:
                print(f"Error: {e}", file=sys.stderr)
                return 1
        else:
            mgr.set_engine_value(engine_key, value)
        print(f"Set {key} = {value}")
        return 0
    elif key == "model":
        mgr.set_model(value)
        print(f"Set model = {value}")
        return 0
    else:
        print(f"Error: Unknown config key: {key}", file=sys.stderr)
        return 1


def _config_get(mgr: ConfigManager, key: str) -> int:
    """Get a config value using dot notation."""
    parts = key.split(".", 1)
    if len(parts) == 2 and parts[0] == "engine":
        value = mgr.get_engine_value(parts[1])
        if value is None:
            print(f"Not set: {key}", file=sys.stderr)
            return 1
        print(value)
        return 0
    elif key == "model":
        print(mgr.get_model())
        return 0
    else:
        print(f"Error: Unknown config key: {key}", file=sys.stderr)
        return 1

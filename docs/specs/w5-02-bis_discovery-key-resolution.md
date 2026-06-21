# W5-02-bis: Discovery resolves its own API key

## Intent
discover_models currently reads only os.environ[api_key_env]. This is a
hidden ordering dependency — discovery returns empty silently if the env
wasn't primed by _set_api_key_env first. W5-04's MCP tools and any other
caller would hit empty results with no signal. Discovery must resolve its
own key from config, not depend on undocumented env setup.

## What to change

### infrastructure/model_discovery.py — _resolve_api_key
Resolve the key in this order:
1. providers[].api_key from config (the real source — keys live here
   after W5-01-bis migration)
2. os.environ[api_key_env] as fallback (for env-based setups)
3. None → that provider is skipped, logged clearly:
   "No API key for provider X (checked config providers.X.api_key and
   env Y)"

This needs the ProviderConfig to carry api_key (it already does per
W5-01 schema). _resolve_api_key takes the ProviderConfig and checks
.api_key first, then the env var named by .api_key_env.

## Acceptance criteria
- Discovery works when key is ONLY in config.yml providers section,
  with no env priming
- Discovery still works when key is ONLY in env
- Config key takes precedence over env
- Missing key (neither source) → provider skipped with a clear log
  message naming both places checked
- No hidden dependency on _set_api_key_env having run

## Testing
- Unit test: key in config only, no env → discovery works
- Unit test: key in env only, not config → discovery works
- Unit test: key in both → config wins
- Unit test: key in neither → provider skipped, log message names
  both sources
- Full suite passes clean

## Constraints
- Read infrastructure/model_discovery.py _resolve_api_key and
  infrastructure/config.py ProviderConfig before touching
- Touch only model_discovery.py
- The log message on missing key must name both checked locations —
  no silent empty

# W5-01-bis: Remove legacy api_keys config support

## Intent
W5-01 added a backward-compatible path that synthesizes providers from
the old api_keys dict. This is tech debt — snodo is pre-release with a
single operator. Drop the old schema entirely. providers is the only
config shape.

## What to change

### cli/config.py
- Remove the api_keys synthesis fallback added in W5-01
- Remove the old api_keys dict from ConfigManager.load() defaults
- Remove PROVIDER_MODEL_PREFIXES and _API_KEY_ENV_MAP if now unused
  (verify no other consumer first)
- get_key_for_model, add_key, get_key route ONLY through the providers
  section — no old-schema branch
- If a config.yml has api_keys but no providers, raise ConfigLoadError
  with a clear migration message: "Legacy api_keys config detected.
  Migrate to the providers section. See docs."

### Migrate the operator's own config
Provide the exact providers block to drop into ~/.snodo/config.yml
so the operator can migrate their working config in one paste.

## Acceptance criteria
- No api_keys synthesis code remains
- providers is the only supported schema
- A legacy config raises ConfigLoadError with a migration message,
  not a silent fallback
- get_key_for_model / add_key / get_key have no old-schema branch

## Testing
- Unit test: providers config → resolves correctly
- Unit test: legacy api_keys config → ConfigLoadError with migration
  message
- Remove the W5-01 backward-compat tests (synthesized providers) —
  that path no longer exists
- Full suite passes clean

## Constraints
- Read cli/config.py in full before touching anything
- This is a deletion ticket — remove code, don't add compatibility
- Provide the operator's migration block in the closure notes

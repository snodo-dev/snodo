# W5-01: Fold api_keys into a providers config section

## Intent
Today providers are implicit — api_keys is a flat dict and provider
knowledge is hardcoded in PROVIDER_MODEL_PREFIXES and _API_KEY_ENV_MAP
(cli/config.py:23-36, only openai/anthropic/google). To support model
discovery (W5-02) we need an explicit providers section that declares
each provider's credentials AND its /models endpoint. Fold api_keys
into it. Backward-compatible — existing config.yml with api_keys must
keep working.

## What to change

### New config.yml schema
providers:
  anthropic:
    api_key_env: ANTHROPIC_API_KEY
    models_endpoint: https://api.anthropic.com/v1/models
  openrouter:
    api_key_env: OPENROUTER_API_KEY
    models_endpoint: https://openrouter.ai/api/v1/models
  google:
    api_key_env: GEMINI_API_KEY
    models_endpoint: https://generativelanguage.googleapis.com/v1beta/models

Each provider declares: api_key_env (env var name for the credential)
and models_endpoint (OpenAI-standard /models URL for discovery).

### cli/config.py
- Add a ProviderConfig pydantic model (name, api_key_env, models_endpoint)
- Add get_providers() returning the configured providers
- Backward-compat: if config.yml has the old api_keys dict and no
  providers section, synthesize providers from api_keys using the
  existing PROVIDER_MODEL_PREFIXES / _API_KEY_ENV_MAP knowledge.
  Existing configs keep working with zero changes.
- get_key_for_model() must work under both old and new schema — resolve
  the provider for a model, then look up its credential from providers
  (new) or api_keys (old).
- add_key()/get_key() must continue to work — route writes to the
  providers section if present, fall back to api_keys for old configs.

### Known providers seed
Define the default provider catalog (anthropic, openai, openrouter,
google) with their endpoints as a module constant. config.yml can
override or add. Do NOT hardcode this in multiple places — single
source of truth.

## Acceptance criteria
- New providers section parses into ProviderConfig models
- Old config.yml with api_keys still works (synthesized providers)
- get_key_for_model resolves credentials under both schemas
- Default provider catalog includes anthropic, openai, openrouter,
  google with correct /models endpoints
- No code reads the hardcoded PROVIDER_MODEL_PREFIXES directly anymore
  — it routes through get_providers()

## Testing
- Unit test: new providers section → ProviderConfig models
- Unit test: old api_keys-only config → synthesized providers,
  credentials resolve
- Unit test: get_key_for_model under both schemas
- Unit test: default catalog has the four providers with endpoints
- Full suite passes clean

## Constraints
- Read cli/config.py in full before touching anything
- Backward compatibility is non-negotiable — existing user configs
  must not break
- Single source of truth for the default provider catalog
- Do not change how the engine receives model strings — this is
  config/credential resolution only
- This ticket does NOT do discovery — just the config schema. W5-02
  consumes models_endpoint.

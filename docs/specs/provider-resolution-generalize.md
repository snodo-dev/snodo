# Spec: replace PROVIDER_MODEL_PREFIXES allowlist with litellm provider detection

## Why
PROVIDER_MODEL_PREFIXES is a hardcoded allowlist — every new provider
needs a code change (deepseek/ was missing, broke auth). litellm
already detects providers natively via get_llm_provider(). Replace the
allowlist with litellm's detection plus a thin alias map for the one
case litellm can't handle: openai/@cf/ (litellm reports "openai", but
the config block is "cloudflare").

## Behaviour from recon
litellm.get_llm_provider(model) returns (model, provider, _, api_base):
  deepseek/deepseek-v4-flash      -> "deepseek"   ✓ matches config
  gemini/gemini-3.5-flash         -> "gemini"     (config key: google — MAP)
  openai/@cf/google/gemma-...     -> "openai"     (config key: cloudflare — MAP)
  claude-sonnet-4 (no prefix)     -> raises BadRequestError

## Change

### 1. _provider_for_model — use litellm, with alias map + fallback
cli/config.py:149
Replace the prefix-loop with:
  - call litellm.get_llm_provider(model); take the provider name
  - apply a small ALIAS map to reconcile litellm names -> snodo config keys:
        PROVIDER_ALIASES = {
            "gemini": "google",
            # openai/@cf/ -> cloudflare handled below
        }
  - SPECIAL CASE: if model starts with "openai/@cf/", provider = "cloudflare"
    (litellm says openai but we route CF through the openai-compatible
     endpoint; the base_url + key live under the cloudflare config block)
  - on BadRequestError (bare model string with no prefix), fall back to
    the OLD prefix matching so existing unprefixed configs still resolve
    (claude-sonnet-4 etc).

Keep PROVIDER_MODEL_PREFIXES as the FALLBACK only, not the primary path.

### 2. Verify all six call sites still resolve correctly
Recon listed them:
  - cli/config.py:37  (_set_api_key_env)
  - cli/config.py:149 (_provider_for_model itself)
  - coders/litellm.py:78 (_resolve_api_base)
  - engine/loop.py:175 (validator base_url)
  - cli/commands/sandbox_run.py:33
  - infrastructure/memory.py:288 (summary model)
No change needed at the call sites — they all consume _provider_for_model's
result. Just confirm the new resolution returns the same config-key names.

## Tests
- get_llm_provider path: deepseek/, gemini/ (->google), openai/@cf/ (->cloudflare)
- bare model string (claude-sonnet-4) falls back to prefix map, resolves anthropic
- a provider NOT in the old allowlist (e.g. groq/llama-...) now resolves
  without any code change — this is the whole point
- _set_api_key_env and _resolve_api_base still set correct key/base_url
  for cloudflare via the openai/@cf/ alias

## Touch only
cli/config.py

Commit: feat(providers): litellm-native provider detection, allowlist as fallback + CF alias

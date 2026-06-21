# Spec: register deepseek/ in PROVIDER_MODEL_PREFIXES

## Why
deepseek/ has no entry in PROVIDER_MODEL_PREFIXES (config.py:24-29).
_provider_for_model returns None for deepseek/deepseek-v4-flash, so
its api_key never resolves → DeepseekException Authentication Fails.

## Change
cli/config.py PROVIDER_MODEL_PREFIXES — add:
  "deepseek": ["deepseek/"],

Confirm cloudflare entry also matches the openai/@cf/ alias we route
through (we added "openai/@cf/" -> cloudflare earlier — verify it's
present alongside "cloudflare/").

## Tests
- _provider_for_model("deepseek/deepseek-v4-flash") -> "deepseek"
- _provider_for_model("openai/@cf/...") -> "cloudflare"
- deepseek api_key resolves and is set in env before the call

## Touch only
cli/config.py

Commit: fix(providers): register deepseek/ prefix for provider resolution

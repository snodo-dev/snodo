# Spec: Cloudflare Workers AI provider support

## Changes

### 1. ProviderConfig — add account_id fields
infrastructure/config.py:19-23

Add two optional fields:
  account_id: str = ""
  account_id_env: str = ""

### 2. _set_api_key_env — inject account_id env var
cli/config.py:31-40

After setting pc.api_key_env, also set pc.account_id_env if present:
  if pc and pc.account_id_env and pc.account_id:
      os.environ[pc.account_id_env] = pc.account_id

### 3. PROVIDER_MODEL_PREFIXES — add cloudflare
cli/config.py (wherever PROVIDER_MODEL_PREFIXES is defined)

Add: "cloudflare/" -> "cloudflare"

## Config usage after this lands

providers:
  cloudflare:
    api_key: xxx
    api_key_env: CLOUDFLARE_API_KEY
    account_id: yyy
    account_id_env: CLOUDFLARE_ACCOUNT_ID
    models_endpoint: https://api.cloudflare.com/client/v4/accounts/\{account_id\}/ai/models/search

llm:
  validator:
    model: cloudflare/@cf/moonshotai/kimi-k2.7-code

## Tests
- ProviderConfig with account_id fields serialises correctly
- _set_api_key_env sets both CLOUDFLARE_API_KEY and CLOUDFLARE_ACCOUNT_ID
- get_key_for_model("cloudflare/@cf/meta/llama-2-7b") resolves to cloudflare provider

## Touch only
infrastructure/config.py, cli/config.py

Commit: feat(providers): Cloudflare Workers AI support (account_id + env injection)

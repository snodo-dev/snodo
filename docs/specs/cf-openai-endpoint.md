# Spec: route Cloudflare via OpenAI-compatible endpoint (base_url)

## Why
litellm's cloudflare/ provider drops tools + temperature (supports
only stream, max_tokens). CF's OpenAI-compatible endpoint accepts
the full param set. Route CF through litellm's openai/ path with a
base_url override instead.

## Changes

### 1. ProviderConfig — add base_url
infrastructure/config.py:19
Add: base_url: str = ""

### 2. Thread base_url into the completion call as api_base
The validator/coder completion_fn is a functools.partial binding
only model (loop.py ~174). Bind api_base into that partial too when
the provider for the model has a base_url configured.

Resolution: given the model string, look up its provider's
ProviderConfig; if base_url is set, include api_base=base_url in
the partial / completion kwargs. If unset, behaviour unchanged.

Apply at every completion call site that builds the partial:
- validator completion_fn (loop.py)
- coder completion_fn (loop.py / lite_llm_adapter)

### 3. DEFAULT_PROVIDER_CATALOG — update cloudflare entry
infrastructure/config.py
Set cloudflare base_url to:
  https://api.cloudflare.com/client/v4/accounts/\{account_id\}/ai/v1
(account_id substituted at runtime from ProviderConfig.account_id)

## Config after this lands
providers:
  cloudflare:
    api_key: xxx
    api_key_env: CLOUDFLARE_API_KEY
    account_id: b34c59f008d4bdd78b7d2eb223cee7c2
    base_url: https://api.cloudflare.com/client/v4/accounts/b34c59f008d4bdd78b7d2eb223cee7c2/ai/v1

llm:
  validator:
    model: openai/@cf/google/gemma-4-26b-a4b-it

## Tests
- ProviderConfig with base_url serialises
- completion call for a model whose provider has base_url includes
  api_base in kwargs
- model whose provider has no base_url: no api_base passed (unchanged)
- openai/@cf/... routes to CF endpoint with tools + temperature intact

## Touch only
infrastructure/config.py, cli/config.py, engine/loop.py
(+ coder adapter if its completion_fn is built separately)

Commit: feat(providers): route Cloudflare via OpenAI-compatible endpoint (base_url/api_base)

# Spec: snodo models — live provider/model discovery with cache

## Goal
Read-only command listing configured providers and their models with
cost, copy-paste-ready model strings for the dispatcher. Live
discovery, per-provider local cache.

## Command shape
  snodo models
    -> list configured providers from config (deepseek, cloudflare, ...)
       with a hint to run --provider=<name>
  snodo models --provider=deepseek
    -> fetch (or read cache) that provider's models, print table
  snodo models --provider=cloudflare --flush
    -> ignore cache, refetch, update cache
  snodo models --provider=cloudflare --filter=<expr>
    -> filter on parsed attributes

## Discovery
Extend model_discovery.py _DISCOVERY_DISPATCH with cloudflare and
deepseek adapters (alongside existing anthropic/google/openrouter).
- cloudflare: GET {models_endpoint with account_id}, auth
  CLOUDFLARE_API_KEY. (models_endpoint not set on CF ProviderConfig
  today — add it.)
- deepseek: GET https://api.deepseek.com/models, Bearer key.
Keys resolved via existing _resolve_api_key (pc.api_key || pc.api_key_env).

## ModelInfo / full_string prefix rule
- Native litellm providers emit their own prefix: deepseek/{id},
  gemini/{id} (existing), etc.
- Cloudflare emits openai/@cf/{id} (the working snodo prefix — NOT
  bare @cf/ or cloudflare/@cf/).
full_string must be copy-paste-ready into --coding-model / dispatcher.

## Pricing
Look up litellm.model_cost[full_string] (or id) for input/output cost.
CF models priced via the register_model table landed in dad5a3f.
Not found -> "unknown". No new pricing code.

## Filtering (--filter)
Filter on PARSED attributes, not substring grep:
- numeric: context_window, input_cost, output_cost
  (e.g. --filter="context_window>100000", --filter="output_cost<1")
- string contains: id / display_name
Define a small filter expr parser: <field><op><value>, ops > < >= <= =
for numeric, substring match for id/name.
NO supports_tools in v1 (needs per-model detail calls — deferred).

## Cache
- ~/.snodo/models/<provider>.json
- shape: {fetched_at: <epoch>, provider, models:[ModelInfo...]}
- TTL: now - fetched_at > 86400 -> stale, refetch
- --flush: bypass cache, refetch, rewrite file
- .snodo/ is gitignored (local-only) — confirm

## Output (match list_jobs table style, job_cmd.py:63-70)
PROVIDER    MODEL                              CONTEXT   COST in/out (per 1M)
----------  ---------------------------------  --------  --------------------
cloudflare  openai/@cf/google/gemma-4-26b...   256000    $0.10 / $0.30
deepseek    deepseek/deepseek-v4-flash         128000    $0.14 / $0.28

## Tests
- --provider=deepseek fetches, caches to ~/.snodo/models/deepseek.json
- second call within 24h reads cache (no network)
- --flush forces refetch even with fresh cache
- cloudflare full_string emits openai/@cf/... (copy-ready)
- deepseek full_string emits deepseek/...
- --filter="context_window>100000" excludes smaller-context models
- --filter="output_cost<1" excludes pricey models
- price falls back to "unknown" when not in litellm.model_cost
- bare `snodo models` lists configured providers

## Touch
snodo/infrastructure/model_discovery.py, infrastructure/config.py
(CF models_endpoint + deepseek catalog entry), new
cli/commands/models_cmd.py, main.py (command registration)

Commit: feat(cli): snodo models — live discovery, per-provider cache, attribute filtering

# Spec: pricing + metadata from models.dev catalog (drop hardcoded table)

## Why
Pricing is hardcoded via litellm.register_model — brittle, manual,
incomplete (DeepSeek v4 shows "unknown"). models.dev/catalog.json is a
live MIT catalog with per-model cost, context, and capabilities.
Confirmed shape: catalog[provider].models[model] = {cost:{input,output},
limit:{context,output}, tool_call, reasoning, structured_output, ...}.

## New: catalog lookup layer (infrastructure/pricing.py or model_catalog.py)
- fetch https://models.dev/catalog.json, cache to ~/.snodo/
  models_dev_catalog.json, 24h TTL, refetch on miss/stale.
- lookup(model_string) -> {input_cost, output_cost, context, tool_call,
  reasoning} or None.
- Normalize OUR model strings to catalog keys:
    deepseek/deepseek-v4-flash -> catalog["deepseek"].models["deepseek-v4-flash"]
    gemini/X                   -> catalog["google"].models["X"]  (alias)
    openai/@cf/<rest>          -> catalog["cloudflare"].models["<rest>"] (strip openai/@cf/)
    claude-X (no prefix)       -> catalog["anthropic"].models["claude-X"]
  Reuse the provider-alias logic from _provider_for_model where possible.

## Consumers — route all pricing through the catalog layer
1. cli/commands/models_cmd.py _lookup_price: catalog first, then
   litellm.model_cost, then "unknown". Also fill CONTEXT from
   catalog limit.context (currently "—" for most providers).
2. infrastructure/usage_tracker.py: cost — prefer catalog price *
   tokens; keep litellm.completion_cost as fallback. (So meta cost is
   accurate for DeepSeek, currently null.)
3. coders/litellm.py register_model block: DROP the hardcoded CF
   prices once catalog covers them. Keep register_model ONLY for
   models genuinely absent from models.dev (if any) — else remove
   entirely.

## Fallback order everywhere
models.dev catalog -> litellm.model_cost -> "unknown".

## Cache
~/.snodo/models_dev_catalog.json, {fetched_at, catalog}, 24h TTL.
One fetch, reused across models_cmd + usage_tracker.

## Tests
- catalog lookup: deepseek/deepseek-v4-flash -> real cost (not unknown)
- openai/@cf/google/gemma-4-26b-a4b-it normalizes + resolves price
- gemini/ and bare claude- normalize correctly
- context populated from catalog (not "—") where catalog has it
- model absent from catalog -> litellm fallback -> "unknown"
- usage_tracker cost uses catalog when completion_cost returns None
- catalog cached, second call no network; stale -> refetch

## Touch
new infrastructure/model_catalog.py (or pricing.py),
cli/commands/models_cmd.py, infrastructure/usage_tracker.py,
coders/litellm.py (remove hardcoded register_model prices)

Commit: feat(pricing): models.dev catalog for cost/context/caps, drop hardcoded table

# Spec: fix double @cf/ prefix in cloudflare model discovery

## Bug
snodo models --provider=cloudflare emits "openai/@cf/@cf/..." — double
@cf/. CF's /models endpoint returns ids ALREADY prefixed with @cf/
(e.g. "@cf/meta/llama-3.2-3b-instruct"). The adapter prepends
"openai/@cf/" on top, producing the double prefix.

Consequences:
- full_string not copy-paste valid (won't resolve in dispatcher)
- pricing lookup misses: register_model keys use single @cf/
  ("openai/@cf/google/gemma-4-26b-a4b-it"), discovery produces double,
  so litellm.model_cost lookup returns unknown for ALL CF models.

## Fix
model_discovery.py _discover_cloudflare full_string construction:
emit "openai/{id}" where id already carries the @cf/ prefix from the
endpoint — i.e. "openai/" + "@cf/meta/..." = "openai/@cf/meta/...".
Do NOT add a second @cf/.

If any ids come back WITHOUT @cf/, normalize to ensure exactly one
@cf/ segment. Net rule: full_string = "openai/@cf/<rest>" with exactly
one @cf/.

## Verify
After fix:
- openai/@cf/google/gemma-4-26b-a4b-it (single @cf) — matches the
  register_model key → price shows $0.10/$0.30, not unknown
- copy-paste of full_string resolves via _provider_for_model to
  cloudflare and runs

## Tests
- _discover_cloudflare emits single-@cf full_string for an id that
  arrives as "@cf/google/gemma-4-26b-a4b-it"
- pricing lookup hits for a registered CF model (not unknown)
- no full_string contains "@cf/@cf/"

## Touch only
infrastructure/model_discovery.py

Commit: fix(models): cloudflare double @cf/ prefix broke full_string and pricing lookup

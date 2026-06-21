# W5-02: Model discovery across providers

## Intent
Discover available models from each configured provider's models_endpoint.
The three providers have DIFFERENT auth and response shapes (verified):
- Anthropic: X-Api-Key + anthropic-version headers, {"data":[{"id"}]}
- OpenRouter: Bearer auth, {"data":[{"id"}]} (extra fields ignored)
- Google: ?key= query param, {"models":[{"name":"models/..."}]} camelCase
So discovery needs a per-provider adapter, not one OpenAI-standard parser.
Results cached 24h. Failure returns cached/empty, never blocks.

## Prerequisite refactor
Move ProviderConfig and DEFAULT_PROVIDER_CATALOG from cli/config.py to
infrastructure/config.py (avoids infrastructure→cli import). Update
cli/config.py to import them back from infrastructure.config. Verify
no other consumer breaks.

## Dependency
Add httpx to pyproject.toml dependencies.

## What to build

### infrastructure/model_discovery.py

ModelInfo (pydantic BaseModel):
  provider: str          # "anthropic", "openrouter", "google"
  id: str                # the model id used for routing (provider-normalized)
  full_string: str       # what gets passed to the coder/adapter
  display_name: str = ""
  context_window: int = 0

Per-provider discovery adapters — one function or small class each:
  _discover_anthropic(cfg) -> list[ModelInfo]
    GET endpoint, headers: X-Api-Key, anthropic-version: 2023-06-01
    parse data[].id
  _discover_openrouter(cfg) -> list[ModelInfo]
    GET endpoint, header: Authorization: Bearer
    parse data[].id (ignore extra fields)
  _discover_google(cfg) -> list[ModelInfo]
    GET endpoint?key=KEY
    parse models[].name, strip "models/" prefix for id

Dispatch by provider name. Unknown provider → log + skip.

discover_models(providers: dict[str, ProviderConfig],
                force_refresh: bool = False) -> list[ModelInfo]
  - 24h TTL cache (file-based in ~/.snodo/ or in-memory with timestamp —
    file-based survives restarts, prefer that)
  - On cache hit within TTL: return cached
  - On miss or force_refresh: fetch all providers, cache, return
  - On any provider fetch failure: log, use cached for that provider if
    available, otherwise skip that provider — never raise, never block
  - Resolve api key from the provider's api_key_env or config

## Acceptance criteria
- ProviderConfig + DEFAULT_PROVIDER_CATALOG live in infrastructure/config.py
- httpx added as dependency
- Each provider's correct auth + response shape handled
- Google "models/" prefix stripped from id
- discover_models caches 24h, force_refresh bypasses
- Provider fetch failure does not raise — returns cached/partial
- ModelInfo is pydantic

## Testing
- Unit test per provider: mock httpx response → correct ModelInfo list
- Unit test: Google models/ prefix stripped
- Unit test: cache hit within TTL → no HTTP call
- Unit test: force_refresh → HTTP call even with fresh cache
- Unit test: provider fetch raises → discover_models returns partial,
  does not raise
- Unit test: ProviderConfig importable from infrastructure.config
- Full suite passes clean

## Constraints
- Read cli/config.py (ProviderConfig, DEFAULT_PROVIDER_CATALOG) before
  the move
- Mock all httpx calls in tests — no live network in the test suite
- Per-provider response parsing — do not assume OpenAI-standard
- Never let a network failure block discovery

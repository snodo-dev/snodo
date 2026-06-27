"""Model discovery across configured providers.

FILE: snodo/infrastructure/model_discovery.py

Fetches available models from each provider's models_endpoint.
Per-provider response parsing — Anthropic, OpenRouter, and Google
have different auth and response shapes.

Cached 24h in a simple JSON file under ~/.snodo/.
"""

import json
import logging
import os
import time
from pathlib import Path
from typing import Dict, List, Optional

from pydantic import BaseModel

from snodo.infrastructure.config import ProviderConfig
from snodo.infrastructure.paths import resolve_home

_logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 24 * 3600


class ModelInfo(BaseModel):
    """A discovered model from a provider's /models endpoint."""
    provider: str
    id: str
    full_string: str   # pass this to the coder/adapter
    display_name: str = ""
    context_window: int = 0


def _resolve_api_key(provider_name: str, pc: ProviderConfig) -> Optional[str]:
    """Resolve the API key: config value first, then env fallback.

    Args:
        provider_name: Provider name for logging (e.g. "anthropic").
        pc: ProviderConfig with optional api_key and api_key_env.

    Returns:
        API key string, or None if neither source has one.
    """
    # 1. Config-stored key (from ~/.snodo/config.yml providers.<name>.api_key)
    if pc.api_key:
        return pc.api_key

    # 2. Environment variable fallback
    if pc.api_key_env:
        env_val = os.environ.get(pc.api_key_env)
        if env_val:
            return env_val

    # 3. Neither source has a key — log clearly
    sources = []
    if pc.api_key_env:
        sources.append(f"env:{pc.api_key_env}")
    sources.append("config api_key")
    _logger.warning(
        "No API key for %s (tried: %s)",
        provider_name,
        ", ".join(reversed(sources)),
    )
    return None


# ------------------------------------------------------------------#
# Per-provider discovery adapters
# ------------------------------------------------------------------#

def _discover_anthropic(pc: ProviderConfig) -> List[ModelInfo]:
    """GET /models with X-Api-Key + anthropic-version headers."""
    import httpx

    api_key = _resolve_api_key("anthropic", pc)
    if not api_key:
        return []

    try:
        resp = httpx.get(
            pc.models_endpoint,
            headers={
                "X-Api-Key": api_key,
                "anthropic-version": "2023-06-01",
            },
            timeout=15.0,
        )
        resp.raise_for_status()
    except Exception as e:
        _logger.warning("Anthropic model discovery failed: %s", e)
        return []

    data = resp.json()
    results = []
    for item in data.get("data", []):
        mid = item.get("id", "")
        if mid:
            results.append(ModelInfo(
                provider="anthropic",
                id=mid,
                full_string=mid,
                display_name=item.get("display_name", mid),
            ))
    return results


def _discover_openrouter(pc: ProviderConfig) -> List[ModelInfo]:
    """GET /models with Bearer auth. data[].id — extra fields ignored."""
    import httpx

    api_key = _resolve_api_key("openrouter", pc)
    if not api_key:
        return []

    try:
        resp = httpx.get(
            pc.models_endpoint,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=15.0,
        )
        resp.raise_for_status()
    except Exception as e:
        _logger.warning("OpenRouter model discovery failed: %s", e)
        return []

    data = resp.json()
    results = []
    for item in data.get("data", []):
        mid = item.get("id", "")
        if mid:
            results.append(ModelInfo(
                provider="openrouter",
                id=mid,
                full_string=mid,
            ))
    return results


def _discover_google(pc: ProviderConfig) -> List[ModelInfo]:
    """GET /models?key=KEY. models[].name camelCase, strip "models/" prefix."""
    import httpx

    api_key = _resolve_api_key("google", pc)
    if not api_key:
        return []

    try:
        resp = httpx.get(
            pc.models_endpoint,
            params={"key": api_key},
            timeout=15.0,
        )
        resp.raise_for_status()
    except Exception as e:
        _logger.warning("Google model discovery failed: %s", e)
        return []

    data = resp.json()
    results = []
    for item in data.get("models", []):
        name = item.get("name", "")
        if name:
            # Strip "models/" prefix for a clean id
            clean_id = name.removeprefix("models/")
            results.append(ModelInfo(
                provider="google",
                id=clean_id,
                full_string=f"gemini/{clean_id}",
                display_name=item.get("displayName", clean_id),
                context_window=item.get("inputTokenLimit", 0)
                or item.get("maxInputTokens", 0),
            ))
    return results


def _substitute_account_id(url: str, pc: ProviderConfig) -> str:
    """Replace {account_id} in *url* with pc.account_id or env var."""
    if "{account_id}" not in url:
        return url
    account_id = pc.account_id
    if not account_id and pc.account_id_env:
        account_id = os.environ.get(pc.account_id_env, "")
    return url.replace("{account_id}", account_id)


def _discover_cloudflare(pc: ProviderConfig) -> List[ModelInfo]:
    """GET /models/search?task=text-generation with Bearer auth."""
    import httpx

    api_key = _resolve_api_key("cloudflare", pc)
    if not api_key:
        return []

    endpoint = _substitute_account_id(pc.models_endpoint, pc)

    try:
        resp = httpx.get(
            endpoint,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=15.0,
        )
        resp.raise_for_status()
    except Exception as e:
        _logger.warning("Cloudflare model discovery failed: %s", e)
        return []

    data = resp.json()
    results = []
    items = data.get("result", []) if isinstance(data, dict) else data
    if not isinstance(items, list):
        items = []
    for item in items:
        mid = item.get("name") or item.get("id") or item.get("model_id") or ""
        if mid:
            # CF returns ids already prefixed @cf/. Strip it so we emit
            # openai/@cf/{rest} with exactly one @cf/ segment.
            rest = mid.removeprefix("@cf/")
            results.append(ModelInfo(
                provider="cloudflare",
                id=mid,
                full_string=f"openai/@cf/{rest}",
                display_name=item.get("description") or item.get("display_name", mid),
                context_window=item.get("context_window", 0),
            ))
    return results


def _discover_deepseek(pc: ProviderConfig) -> List[ModelInfo]:
    """GET /models with Bearer auth."""
    import httpx

    api_key = _resolve_api_key("deepseek", pc)
    if not api_key:
        return []

    try:
        resp = httpx.get(
            pc.models_endpoint,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=15.0,
        )
        resp.raise_for_status()
    except Exception as e:
        _logger.warning("DeepSeek model discovery failed: %s", e)
        return []

    data = resp.json()
    results = []
    items = data.get("data", []) if isinstance(data, dict) else data
    if not isinstance(items, list):
        items = []
    for item in items:
        mid = item.get("id", "")
        if mid:
            results.append(ModelInfo(
                provider="deepseek",
                id=mid,
                full_string=f"deepseek/{mid}",
                display_name=item.get("display_name", mid),
                context_window=item.get("context_window", 0),
            ))
    return results


_DISCOVERY_DISPATCH = {
    "anthropic": _discover_anthropic,
    "openrouter": _discover_openrouter,
    "google": _discover_google,
    "cloudflare": _discover_cloudflare,
    "deepseek": _discover_deepseek,
}


def _cache_path() -> Path:
    return resolve_home() / "model_cache.json"


def _read_cache() -> Optional[List[dict]]:
    cp = _cache_path()
    if not cp.exists():
        return None
    try:
        data = json.loads(cp.read_text())
        age = time.time() - data.get("timestamp", 0)
        if age < _CACHE_TTL_SECONDS:
            return data.get("models", [])
    except Exception:
        pass
    return None


def _write_cache(models: List[dict]) -> None:
    cp = _cache_path()
    cp.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "timestamp": time.time(),
        "models": models,
    }
    cp.write_text(json.dumps(payload, indent=2))


def discover_models(
    providers: Dict[str, ProviderConfig],
    force_refresh: bool = False,
) -> List[ModelInfo]:
    """Discover available models from all configured providers.

    Cached 24h.  On cache miss or *force_refresh*, fetches from every
    provider.  A single provider failure does not block discovery of
    others — its previous cached data is retained if available, otherwise
    it is skipped.

    Args:
        providers: Dict of provider_name → ProviderConfig
        force_refresh: If True, bypass the cache and re-fetch

    Returns:
        List of ModelInfo objects across all providers.
    """
    if not force_refresh:
        cached = _read_cache()
        if cached is not None:
            return [ModelInfo(**m) for m in cached]

    all_models: List[ModelInfo] = []
    failed: List[str] = []

    for name, pc in providers.items():
        discover_fn = _DISCOVERY_DISPATCH.get(name)
        if discover_fn is None:
            continue
        try:
            results = discover_fn(pc)
        except Exception as e:
            _logger.warning("Discovery failed for %s: %s", name, e)
            results = []

        if results:
            all_models.extend(results)
        else:
            failed.append(name)

    if not all_models and failed:
        # All providers failed — try returning cached even if stale
        raw = _read_stale_cache()
        if raw:
            return [ModelInfo(**m) for m in raw]

    # Cache whatever we got
    serialized = [m.model_dump() for m in all_models]
    _write_cache(serialized)
    return all_models


def _read_stale_cache() -> Optional[List[dict]]:
    """Read cache even if TTL expired (last-resort fallback)."""
    cp = _cache_path()
    if not cp.exists():
        return None
    try:
        data = json.loads(cp.read_text())
        return data.get("models", [])
    except Exception:
        return None

"""Model pricing / capability catalog from models.dev.

FILE: snodo/infrastructure/model_catalog.py

Fetches and caches models.dev/catalog.json for per-model pricing,
context window, and capability metadata.  Fallback: litellm.model_cost
then "unknown".

Lookup normalises snodo model strings to catalog keys:
  deepseek/deepseek-v4-flash  -> providers.deepseek.models["deepseek-v4-flash"]
  gemini/X                    -> providers.google.models["X"]
  openai/@cf/<rest>           -> providers.cloudflare.models["<rest>"]
  claude-{X} (bare)           -> providers.anthropic.models["claude-{X}"]
"""

import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

from snodo.cli.config import ConfigManager
from snodo.infrastructure.paths import resolve_home

_logger = logging.getLogger(__name__)

_CATALOG_URL = "https://models.dev/catalog.json"
_CACHE_FILE = "models_dev_catalog.json"
_CACHE_TTL = 24 * 3600


def _cache_path() -> Path:
    return resolve_home() / _CACHE_FILE


def _fetch_catalog() -> Optional[dict]:
    """Fetch catalog from models.dev, returns parsed JSON or None."""
    import httpx
    try:
        resp = httpx.get(_CATALOG_URL, timeout=30.0)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        _logger.warning("Failed to fetch catalog from models.dev: %s", e)
        return None


def _read_cached() -> Optional[dict]:
    cp = _cache_path()
    if not cp.exists():
        return None
    try:
        data = json.loads(cp.read_text())
        age = time.time() - data.get("fetched_at", 0)
        if age < _CACHE_TTL:
            return data.get("catalog")
    except Exception:
        pass
    return None


def _write_cache(catalog: dict) -> None:
    cp = _cache_path()
    cp.parent.mkdir(parents=True, exist_ok=True)
    cp.write_text(json.dumps({"fetched_at": time.time(), "catalog": catalog}, indent=2))


def get_catalog() -> Optional[dict]:
    """Return the catalog dict (cached or fresh), or None on failure."""
    cached = _read_cached()
    if cached is not None:
        return cached
    fresh = _fetch_catalog()
    if fresh is not None:
        _write_cache(fresh)
        return fresh
    # Stale cache as last resort
    cp = _cache_path()
    if cp.exists():
        try:
            return json.loads(cp.read_text()).get("catalog")
        except Exception:
            pass
    return None


def _normalise(model: str) -> tuple[Optional[str], Optional[str]]:
    """Return (provider_name, model_id) or (None, None).

    Normalizes snodo model strings for catalog.providers[provider].models[model_id].
    """
    if not model:
        return None, None

    provider = ConfigManager._provider_for_model(model)
    if not provider:
        return None, None

    # Strip the provider/path prefix to get the model_id
    model_lower = model.lower()

    if provider == "cloudflare":
        # openai/@cf/google/gemma-4-26b-a4b-it -> google/gemma-4-26b-a4b-it
        # strip "openai/@cf/"
        prefix = "openai/@cf/"
        if model_lower.startswith(prefix):
            return provider, model[len(prefix):]
        return provider, model

    if provider == "google":
        # gemini/gemini-2.0-flash-exp -> gemini-2.0-flash-exp
        prefix = "gemini/"
        if model_lower.startswith(prefix):
            return provider, model[len(prefix):]
        return provider, model

    if provider == "deepseek":
        # deepseek/deepseek-v4-flash -> deepseek-v4-flash
        prefix = "deepseek/"
        if model_lower.startswith(prefix):
            return provider, model[len(prefix):]
        return provider, model

    # anthropic, openai: bare model string like "claude-sonnet-4-20250514"
    return provider, model


def lookup(model: str) -> dict[str, Any]:
    """Look up model metadata: input_cost, output_cost, context, tool_call, reasoning.

    Returns a dict with all keys present (fallback values for missing keys).
    """
    result: dict[str, Any] = {
        "input_cost": "unknown",
        "output_cost": "unknown",
        "context": 0,
        "tool_call": False,
        "reasoning": False,
    }

    provider, model_id = _normalise(model)
    if not provider or not model_id:
        return _litellm_fallback(model, result)

    catalog = get_catalog()
    if not catalog:
        return _litellm_fallback(model, result)

    provider_data = catalog.get("providers", {}).get(provider, {})
    model_data = provider_data.get("models", {}).get(model_id, {})

    if not model_data:
        return _litellm_fallback(model, result)

    cost = model_data.get("cost", {})
    if isinstance(cost, dict):
        inp = cost.get("input")
        outp = cost.get("output")
        if inp is not None:
            result["input_cost"] = inp
        if outp is not None:
            result["output_cost"] = outp

    limits = model_data.get("limit", {})
    if isinstance(limits, dict):
        ctx = limits.get("context")
        if ctx is not None:
            result["context"] = int(ctx)

    if model_data.get("tool_call"):
        result["tool_call"] = True
    if model_data.get("reasoning"):
        result["reasoning"] = True

    return result


def _litellm_fallback(model: str, result: dict) -> dict:
    """Fall back to litellm.model_cost for pricing."""
    try:
        import litellm
        info = litellm.model_cost.get(model)
        if info:
            inp = info.get("input_cost_per_token")
            outp = info.get("output_cost_per_token")
            if inp is not None:
                result["input_cost"] = inp
            if outp is not None:
                result["output_cost"] = outp
            ctx = info.get("max_input_tokens") or info.get("max_tokens") or info.get("context_window")
            if ctx:
                result["context"] = int(ctx)
            return result
    except Exception:
        pass
    return result

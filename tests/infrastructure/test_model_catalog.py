"""Tests for the models.dev catalog lookup normalisation.

FILE: tests/infrastructure/test_model_catalog.py

Pins the contract that makes `snodo models` resolve metadata for providers
the operator configured themselves:

- a configured provider whose block name differs from its catalog name
  resolves its models' metadata (via the block's ``catalog_provider``),
- a model id containing a colon or a slash survives normalisation,
- the three existing special cases (cloudflare, google, deepseek) keep
  resolving exactly as they do now,
- a provider with no catalog entry reports unknown rather than raising.
"""

import json
import time

import pytest

from snodo.infrastructure import model_catalog
from snodo.infrastructure.model_catalog import _normalise, lookup


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    """A models.dev-style catalog on disk, with a fresh cache path."""
    monkeypatch.setattr(model_catalog, "_cache_path", lambda: tmp_path / "catalog.json")
    catalog = {
        "providers": {
            "ollama-cloud": {
                "models": {
                    "deepseek-v4-flash:0731": {
                        "cost": None,  # subscription plan — genuinely unknown
                        "limit": {"context": 1048576},
                        "tool_call": True,
                    },
                    "deepseek-v4-pro": {
                        "cost": None,
                        "limit": {"context": 1048576},
                    },
                    "llama3:8b": {
                        "cost": {"input": 0.0000001, "output": 0.0000002},
                        "limit": {"context": 8192},
                    },
                },
            },
            "cloudflare": {
                "models": {
                    "@cf/google/gemma-4-26b-a4b-it": {
                        "cost": {"input": 0.1, "output": 0.3},
                        "limit": {"context": 262144},
                    },
                },
            },
            "google": {
                "models": {
                    "gemini-2.0-flash-exp": {
                        "cost": {"input": 0.0000001, "output": 0.0000004},
                        "limit": {"context": 1048576},
                    },
                },
            },
            "deepseek": {
                "models": {
                    "deepseek-v4-flash": {
                        "cost": {"input": 0.0000001, "output": 0.0000002},
                        "limit": {"context": 131072},
                    },
                },
            },
        }
    }
    (tmp_path / "catalog.json").write_text(json.dumps({
        "fetched_at": time.time(),
        "catalog": catalog,
    }))
    return catalog


def _configure_provider(monkeypatch, name, **fields):
    """Register a provider block named *name* with the given fields."""
    from snodo.config import ConfigManager, ProviderConfig

    pc = ProviderConfig(**fields)
    monkeypatch.setattr(
        ConfigManager,
        "get_providers",
        lambda self: {name: pc},
    )
    return pc


# ---------------------------------------------------------------------------
# 1. A configured provider whose block name differs from its catalog name
# ---------------------------------------------------------------------------


def test_block_name_differs_from_catalog_name_resolves_metadata(catalog, monkeypatch):
    """ollama block -> ollama-cloud catalog key via catalog_provider."""
    _configure_provider(monkeypatch, "ollama", catalog_provider="ollama-cloud")

    meta = lookup("ollama/deepseek-v4-flash:0731")
    # cost is genuinely None in the catalog (subscription) — report honestly
    assert meta["input_cost"] == "unknown"
    assert meta["output_cost"] == "unknown"
    # the context window is present and must appear
    assert meta["context"] == 1048576
    assert meta["tool_call"] is True


def test_block_name_defaults_to_catalog_name(catalog, monkeypatch):
    """Without catalog_provider, the block name is the catalog key."""
    _configure_provider(monkeypatch, "ollama-cloud")

    meta = lookup("ollama-cloud/llama3:8b")
    assert meta["input_cost"] == 0.0000001
    assert meta["output_cost"] == 0.0000002
    assert meta["context"] == 8192


def test_normalise_uses_catalog_provider_key(catalog, monkeypatch):
    """_normalise returns the catalog provider, not the block name."""
    _configure_provider(monkeypatch, "ollama", catalog_provider="ollama-cloud")
    provider, model_id = _normalise("ollama/deepseek-v4-flash:0731")
    assert provider == "ollama-cloud"
    assert model_id == "deepseek-v4-flash:0731"


# ---------------------------------------------------------------------------
# 2. A model id containing a colon or a slash survives normalisation
# ---------------------------------------------------------------------------


def test_model_id_with_colon_survives(catalog, monkeypatch):
    _configure_provider(monkeypatch, "ollama", catalog_provider="ollama-cloud")
    provider, model_id = _normalise("ollama/deepseek-v4-flash:0731")
    assert model_id == "deepseek-v4-flash:0731"
    assert provider == "ollama-cloud"


def test_model_id_with_slash_survives(catalog, monkeypatch):
    """Only the leading '<block>/' is stripped; a slash inside the id stays."""
    _configure_provider(monkeypatch, "ollama", catalog_provider="ollama-cloud")
    provider, model_id = _normalise("ollama/org/model:tag")
    assert provider == "ollama-cloud"
    assert model_id == "org/model:tag"


def test_bare_model_id_without_prefix_is_unattributable(catalog, monkeypatch):
    """A bare model string with no block prefix cannot be attributed to a block.

    ``_provider_for_model`` resolves the provider from the prefix; without one
    the lookup honestly returns ``(None, None)`` rather than guessing.
    """
    _configure_provider(monkeypatch, "ollama", catalog_provider="ollama-cloud")
    provider, model_id = _normalise("deepseek-v4-flash:0731")
    assert provider is None
    assert model_id is None


# ---------------------------------------------------------------------------
# 3. The three existing special cases keep resolving exactly as they do now
# ---------------------------------------------------------------------------


def test_cloudflare_special_case_unchanged(catalog, monkeypatch):
    """openai/@cf/<rest> -> cloudflare.models["@cf/<rest>"]."""
    _configure_provider(monkeypatch, "cloudflare")
    provider, model_id = _normalise("openai/@cf/google/gemma-4-26b-a4b-it")
    assert provider == "cloudflare"
    assert model_id == "@cf/google/gemma-4-26b-a4b-it"
    meta = lookup("openai/@cf/google/gemma-4-26b-a4b-it")
    assert meta["context"] == 262144
    assert meta["input_cost"] == 0.1
    assert meta["output_cost"] == 0.3
    assert meta["cost_unit"] == "per_1m"
    assert meta["found"] is True


def test_google_special_case_unchanged(catalog, monkeypatch):
    """gemini/<id> -> google.models["<id>"]."""
    _configure_provider(monkeypatch, "google")
    provider, model_id = _normalise("gemini/gemini-2.0-flash-exp")
    assert provider == "google"
    assert model_id == "gemini-2.0-flash-exp"
    meta = lookup("gemini/gemini-2.0-flash-exp")
    assert meta["context"] == 1048576


def test_deepseek_special_case_unchanged(catalog, monkeypatch):
    """deepseek/<id> -> deepseek.models["<id>"]."""
    _configure_provider(monkeypatch, "deepseek")
    provider, model_id = _normalise("deepseek/deepseek-v4-flash")
    assert provider == "deepseek"
    assert model_id == "deepseek-v4-flash"
    meta = lookup("deepseek/deepseek-v4-flash")
    assert meta["context"] == 131072


# ---------------------------------------------------------------------------
# 4. A provider with no catalog entry reports unknown rather than raising
# ---------------------------------------------------------------------------


def test_provider_with_no_catalog_entry_reports_unknown(catalog, monkeypatch):
    """A block with no catalog entry must not raise; it reports unknown."""
    _configure_provider(monkeypatch, "my-local", catalog_provider="nonexistent-provider")
    meta = lookup("my-local/some-model")
    assert meta["input_cost"] == "unknown"
    assert meta["output_cost"] == "unknown"
    assert meta["context"] == 0


def test_model_with_no_catalog_entry_reports_unknown(catalog, monkeypatch):
    """A model id absent from a known provider reports unknown, not a raise."""
    _configure_provider(monkeypatch, "ollama", catalog_provider="ollama-cloud")
    meta = lookup("ollama/not-in-catalog")
    assert meta["input_cost"] == "unknown"
    assert meta["context"] == 0
    assert meta["found"] is False


def test_model_with_tag_resolves_when_catalog_has_untagged(catalog, monkeypatch):
    """A :tag id resolves when the catalog carries the model untagged."""
    _configure_provider(monkeypatch, "ollama", catalog_provider="ollama-cloud")
    meta = lookup("ollama/deepseek-v4-pro:0813")
    assert meta["found"] is True
    assert meta["context"] == 1048576
    assert meta["input_cost"] == "unknown"


def test_model_with_tag_resolves_when_catalog_has_tagged(catalog, monkeypatch):
    """A :tag id resolves when the catalog carries the model with the tag."""
    _configure_provider(monkeypatch, "ollama", catalog_provider="ollama-cloud")
    meta = lookup("ollama/deepseek-v4-flash:0731")
    assert meta["found"] is True
    assert meta["context"] == 1048576


def test_found_without_price_is_distinguishable_from_not_found(catalog, monkeypatch):
    """A model found with no published price reports found=True; a model not
    found at all reports found=False."""
    _configure_provider(monkeypatch, "ollama", catalog_provider="ollama-cloud")
    found = lookup("ollama/deepseek-v4-flash:0731")
    assert found["found"] is True
    assert found["input_cost"] == "unknown"

    missing = lookup("ollama/not-in-catalog")
    assert missing["found"] is False
    assert missing["input_cost"] == "unknown"


def test_no_catalog_on_disk_reports_unknown(tmp_path, monkeypatch):
    """No catalog at all: fall back to litellm, never raise."""
    monkeypatch.setattr(model_catalog, "_cache_path", lambda: tmp_path / "missing.json")
    monkeypatch.setattr(model_catalog, "_fetch_catalog", lambda: None)
    _configure_provider(monkeypatch, "ollama", catalog_provider="ollama-cloud")
    meta = lookup("ollama/deepseek-v4-flash:0731")
    assert meta["input_cost"] == "unknown"
    assert meta["context"] == 0


# ---------------------------------------------------------------------------
# 5. The cache TTL is unchanged
# ---------------------------------------------------------------------------


def test_cache_ttl_unchanged():
    """The fix must not fetch the catalog more often than the existing TTL."""
    assert model_catalog._CACHE_TTL == 24 * 3600

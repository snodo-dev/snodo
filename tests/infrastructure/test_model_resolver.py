"""Tests for model resolution (infrastructure/model_resolver.py).

Pure logic — no mocks needed.
"""


from snodo.infrastructure.model_discovery import ModelInfo
from snodo.infrastructure.model_resolver import resolve_model, _normalize


# === Helpers ===

def _mi(provider: str, full_string: str, id: str = "", display_name: str = "") -> ModelInfo:
    return ModelInfo(
        provider=provider,
        id=id or full_string,
        full_string=full_string,
        display_name=display_name or full_string,
    )


# === Normalization ===

def test_normalize_removes_hyphens():
    assert _normalize("gpt-4o") == "gpt4o"
    assert _normalize("claude-sonnet-4-20250514") == "claudesonnet420250514"


def test_normalize_removes_dots_slashes_underscores():
    assert _normalize("gemini/gemini-2.0-flash") == "geminigemini20flash"
    assert _normalize("openai/gpt_4o") == "openaigpt4o"


def test_normalize_lowercases():
    assert _normalize("GPT-4O") == "gpt4o"
    assert _normalize("Claude-Sonnet") == "claudesonnet"


# === Exact matches ===

def test_exact_full_string_match():
    candidates = [
        _mi("anthropic", "claude-sonnet-4-20250514"),
        _mi("openai", "gpt-4o"),
    ]
    r = resolve_model("sonnet", candidates)
    assert r.status == "exact"
    assert r.match is not None
    assert r.match.full_string == "claude-sonnet-4-20250514"


def test_exact_normalized_substring():
    """gpt4o matches gpt-4o after normalization."""
    r = resolve_model("gpt4o", [_mi("openai", "gpt-4o")])
    assert r.status == "exact"
    assert r.match is not None
    assert r.match.full_string == "gpt-4o"


def test_exact_matches_bare_name():
    """Query matches just the last segment if full_string has a prefix."""
    r = resolve_model("gemini-2.0-flash", [
        _mi("google", "gemini/gemini-2.0-flash-exp"),
    ])
    assert r.status == "exact"
    assert r.match is not None


def test_exact_case_insensitive():
    r = resolve_model("GPT4O", [_mi("openai", "gpt-4o")])
    assert r.status == "exact"


def test_exact_hyphens_in_query():
    r = resolve_model("claude-sonnet-4", [
        _mi("anthropic", "claude-sonnet-4-20250514"),
    ])
    assert r.status == "exact"


# === Ambiguous ===

def test_ambiguous_returns_all_candidates():
    candidates = [
        _mi("anthropic", "claude-sonnet-4-20250514"),
        _mi("openrouter", "anthropic/claude-sonnet-4-20250514"),
    ]
    r = resolve_model("sonnet", candidates)
    assert r.status == "ambiguous"
    assert r.match is None
    assert len(r.candidates) == 2


def test_ambiguous_gemini_across_providers():
    candidates = [
        _mi("google", "gemini/gemini-2.0-flash-exp"),
        _mi("openrouter", "google/gemini-2.0-flash-exp"),
    ]
    r = resolve_model("gemini", candidates)
    assert r.status == "ambiguous"
    assert len(r.candidates) == 2


def test_specific_gemini_35_resolves():
    """gemini-3.5 matches gemini-2-5-flash via bare name substring."""
    candidates = [
        _mi("google", "gemini/gemini-2.5-flash"),
        _mi("google", "gemini/gemini-2.0-flash-exp"),
    ]
    r = resolve_model("gemini-2.5", candidates)
    assert r.status == "exact"
    assert r.match is not None
    assert "2.5-flash" in r.match.full_string


# === Not Found ===

def test_not_found_echoes_query():
    r = resolve_model("nonexistent", [_mi("openai", "gpt-4o")])
    assert r.status == "not_found"
    assert r.query == "nonexistent"
    assert r.match is None


def test_empty_candidates():
    r = resolve_model("anything", [])
    assert r.status == "not_found"


def test_empty_query():
    r = resolve_model("", [_mi("openai", "gpt-4o")])
    assert r.status == "not_found"


# === Resolution is pydantic ===

def test_resolution_model_dump():
    r = resolve_model("gpt4o", [_mi("openai", "gpt-4o")])
    d = r.model_dump()
    assert d["status"] == "exact"
    assert d["match"]["id"] == "gpt-4o"
    assert d["query"] == "gpt4o"

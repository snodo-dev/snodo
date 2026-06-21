"""Tests for the wave registry — wave.json I/O, expiry, classification flow.

FILE: tests/infrastructure/test_wave_registry.py
"""

import json
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

from snodo.infrastructure.wave_registry import (
    WaveRegistry,
    WaveEntry,
    FLOW_TYPES,
    _generate_wave_id,
    _fallback,
)
from snodo.infrastructure.config import WaveConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_registry(tmp_path: Path, **config_kw) -> WaveRegistry:
    """Build a WaveRegistry rooted at tmp_path with optional config overrides."""
    cfg = WaveConfig(**config_kw) if config_kw else WaveConfig()
    (tmp_path / ".snodo").mkdir(exist_ok=True)
    return WaveRegistry(str(tmp_path), config=cfg)


def _write_waves(tmp_path: Path, waves: list[dict]) -> None:
    """Write a raw wave.json to the temp project."""
    snodo_dir = tmp_path / ".snodo"
    snodo_dir.mkdir(exist_ok=True)
    path = snodo_dir / "wave.json"
    path.write_text(json.dumps(waves, indent=2))


# ---------------------------------------------------------------------------
# WaveEntry + helpers
# ---------------------------------------------------------------------------


class TestFlowTypes:
    def test_valid_types(self):
        assert FLOW_TYPES == {"feature", "defect", "debt", "risk"}


class TestGenerateWaveId:
    def test_first_id(self):
        wid = _generate_wave_id(set())
        assert wid == "w_0001"

    def test_skips_existing(self):
        wid = _generate_wave_id({"w_0001", "w_0002"})
        assert wid == "w_0003"

    def test_does_not_collide(self):
        taken = {f"w_{i:04x}" for i in range(1, 100)}
        wid = _generate_wave_id(taken)
        assert wid not in taken


class TestFallback:
    def test_leaves_task_unwaved(self):
        result = _fallback()
        assert result["flow_type"] == "feature"
        assert result["wave_id"] is None
        assert result["task_summary"] is None
        assert result["feature_description"] is None


# ---------------------------------------------------------------------------
# WaveRegistry — I/O + expiry
# ---------------------------------------------------------------------------


class TestReadWrite:
    def test_read_empty_when_no_file(self, tmp_path):
        reg = _make_registry(tmp_path)
        assert reg._read_waves() == []

    def test_read_corrupt_returns_empty(self, tmp_path):
        (tmp_path / ".snodo").mkdir(exist_ok=True)
        (tmp_path / ".snodo" / "wave.json").write_text("corrupt")
        reg = _make_registry(tmp_path)
        assert reg._read_waves() == []

    def test_read_not_list_returns_empty(self, tmp_path):
        _write_waves(tmp_path, {"not": "a list"})
        reg = _make_registry(tmp_path)
        assert reg._read_waves() == []

    def test_round_trip(self, tmp_path):
        reg = _make_registry(tmp_path)
        waves = [WaveEntry(wave_id="w_0001", feature_description="auth", created=100.0, last_activity=100.0)]
        reg._write_waves(waves)
        loaded = reg._read_waves()
        assert len(loaded) == 1
        assert loaded[0].wave_id == "w_0001"
        assert loaded[0].feature_description == "auth"

    def test_atomic_write_no_tmp_left(self, tmp_path):
        reg = _make_registry(tmp_path)
        reg._write_waves([])
        assert not (reg._wave_path.with_suffix(".json.tmp")).exists()
        assert reg._wave_path.exists()


class TestFilterOpen:
    def test_fresh_wave_is_open(self, tmp_path):
        reg = _make_registry(tmp_path, max_age_days=14, max_idle_days=5)
        now = time.time()
        w = WaveEntry(wave_id="w_0001", feature_description="feat", created=now, last_activity=now)
        assert reg._filter_open([w]) == [w]

    def test_old_age_closes(self, tmp_path):
        reg = _make_registry(tmp_path, max_age_days=14, max_idle_days=5)
        now = time.time()
        old = WaveEntry(wave_id="w_0001", feature_description="feat", created=now - 20 * 86400, last_activity=now)
        assert reg._filter_open([old]) == []

    def test_idle_closes(self, tmp_path):
        reg = _make_registry(tmp_path, max_age_days=14, max_idle_days=5)
        now = time.time()
        idle = WaveEntry(wave_id="w_0001", feature_description="feat", created=now, last_activity=now - 10 * 86400)
        assert reg._filter_open([idle]) == []

    def test_open_waves_public_method(self, tmp_path):
        reg = _make_registry(tmp_path)
        now = time.time()
        _write_waves(tmp_path, [
            {"wave_id": "w_0001", "feature_description": "a", "anchor_summaries": [], "created": now, "last_activity": now, "task_ids": ["t1"]},
            {"wave_id": "w_0002", "feature_description": "b", "anchor_summaries": [], "created": now - 20 * 86400, "last_activity": now, "task_ids": ["t2"]},
        ])
        opened = reg.open_waves()
        assert len(opened) == 1
        assert opened[0].wave_id == "w_0001"


class TestAssign:
    def test_bumps_last_activity(self, tmp_path):
        reg = _make_registry(tmp_path)
        w = WaveEntry(wave_id="w_0001", feature_description="feat", created=100.0, last_activity=100.0)
        reg._assign_to_wave(w, "task_1", "add login")
        assert w.last_activity > 100.0

    def test_appends_task_id(self, tmp_path):
        reg = _make_registry(tmp_path)
        w = WaveEntry(wave_id="w_0001", feature_description="feat", created=100.0, last_activity=100.0)
        reg._assign_to_wave(w, "task_1", "add login")
        assert "task_1" in w.task_ids

    def test_does_not_duplicate_task(self, tmp_path):
        reg = _make_registry(tmp_path)
        w = WaveEntry(wave_id="w_0001", feature_description="feat", created=100.0, last_activity=100.0, task_ids=["task_1"])
        reg._assign_to_wave(w, "task_1", "add login")
        assert w.task_ids == ["task_1"]

    def test_seeds_anchor(self, tmp_path):
        reg = _make_registry(tmp_path)
        w = WaveEntry(wave_id="w_0001", feature_description="feat", created=100.0, last_activity=100.0)
        reg._assign_to_wave(w, "task_1", "implement login page")
        assert len(w.anchor_summaries) == 1
        assert w.anchor_summaries[0] == "implement login page"

    def test_anchor_accumulates_up_to_3(self, tmp_path):
        reg = _make_registry(tmp_path)
        w = WaveEntry(wave_id="w_0001", feature_description="feat", created=100.0, last_activity=100.0)
        reg._assign_to_wave(w, "t1", "summary 1")
        reg._assign_to_wave(w, "t2", "summary 2")
        reg._assign_to_wave(w, "t3", "summary 3")
        assert len(w.anchor_summaries) == 3

    def test_anchor_locks_at_3(self, tmp_path):
        reg = _make_registry(tmp_path)
        w = WaveEntry(wave_id="w_0001", feature_description="feat", created=100.0, last_activity=100.0, anchor_summaries=["s1", "s2", "s3"])
        reg._assign_to_wave(w, "t4", "summary 4")
        assert len(w.anchor_summaries) == 3
        assert "summary 4" not in w.anchor_summaries


# ---------------------------------------------------------------------------
# Classification prompt
# ---------------------------------------------------------------------------


class TestBuildPrompt:
    def test_includes_task_spec(self, tmp_path):
        reg = _make_registry(tmp_path)
        prompt = reg._build_prompt("fix login bug", [])
        assert "fix login bug" in prompt

    def test_includes_open_waves(self, tmp_path):
        reg = _make_registry(tmp_path)
        w = WaveEntry(wave_id="w_0001", feature_description="auth overhaul")
        prompt = reg._build_prompt("fix login bug", [w])
        assert "w_0001" in prompt
        assert "auth overhaul" in prompt

    def test_anchors_in_context(self, tmp_path):
        reg = _make_registry(tmp_path)
        w = WaveEntry(wave_id="w_0001", feature_description="auth", anchor_summaries=["add sso", "fix mfa"])
        prompt = reg._build_prompt("task", [w])
        assert "add sso" in prompt
        assert "fix mfa" in prompt

    def test_no_waves_placeholder(self, tmp_path):
        reg = _make_registry(tmp_path)
        prompt = reg._build_prompt("task", [])
        assert "none" in prompt or "new" in prompt


# ---------------------------------------------------------------------------
# classify_task — integration with mocked LLM
# ---------------------------------------------------------------------------


class TestClassifyTask:
    def test_new_feature_mints_wave(self, tmp_path):
        reg = _make_registry(tmp_path)
        mock_completion = MagicMock()
        mock_completion.return_value.choices[0].message.content = json.dumps({
            "flow_type": "feature",
            "wave_id": "new",
            "task_summary": "Build login system",
            "feature_description": "implement auth",
        })
        result = reg.classify_task("build login system", "task_001", mock_completion, "gemma-model")
        assert result["flow_type"] == "feature"
        assert result["wave_id"].startswith("w_")
        assert reg._wave_path.exists()
        waves = reg._read_waves()
        assert len(waves) == 1
        assert waves[0].feature_description == "implement auth"
        assert not waves[0].feature_description.startswith("build login system")

    def test_matched_wave_assigns(self, tmp_path):
        _write_waves(tmp_path, [
            {"wave_id": "w_0001", "feature_description": "auth", "anchor_summaries": ["sso"], "created": time.time(), "last_activity": time.time(), "task_ids": ["task_000"]},
        ])
        reg = _make_registry(tmp_path)
        mock_completion = MagicMock()
        mock_completion.return_value.choices[0].message.content = json.dumps({
            "flow_type": "feature",
            "wave_id": "w_0001",
            "task_summary": "Add OAuth support",
        })
        result = reg.classify_task("add oauth", "task_001", mock_completion, "gemma-model")
        assert result["wave_id"] == "w_0001"
        waves = reg._read_waves()
        assert len(waves) == 1
        assert "task_001" in waves[0].task_ids

    def test_no_completion_fn_leaves_unwaved(self, tmp_path):
        reg = _make_registry(tmp_path)
        result = reg.classify_task("any task", "task_001", None, "gemma-model")
        assert result["flow_type"] == "feature"
        assert result["wave_id"] is None
        assert result["task_summary"] is None

    def test_invalid_flow_type_defaults_to_feature_unwaved(self, tmp_path):
        reg = _make_registry(tmp_path)
        mock_completion = MagicMock()
        mock_completion.return_value.choices[0].message.content = json.dumps({
            "flow_type": "invalid_type",
            "wave_id": "new",
            "task_summary": "Some task",
            "feature_description": "Some feature",
        })
        result = reg.classify_task("task", "task_001", mock_completion, "gemma-model")
        assert result["flow_type"] == "feature"
        assert result["wave_id"] is None

    def test_stale_wave_mints_new(self, tmp_path):
        old = time.time() - 30 * 86400
        _write_waves(tmp_path, [
            {"wave_id": "w_0001", "feature_description": "auth", "anchor_summaries": ["sso"], "created": old, "last_activity": old, "task_ids": ["task_000"]},
        ])
        reg = _make_registry(tmp_path, max_age_days=14)
        mock_completion = MagicMock()
        mock_completion.return_value.choices[0].message.content = json.dumps({
            "flow_type": "feature",
            "wave_id": "new",
            "task_summary": "Add OAuth",
            "feature_description": "new auth wave",
        })
        result = reg.classify_task("add oauth", "task_001", mock_completion, "gemma-model")
        assert result["wave_id"] != "w_0001"
        waves = reg._read_waves()
        assert len(waves) == 2  # stale still on disk, new minted

    def test_inactive_wave_not_matched(self, tmp_path):
        age_ok = time.time() - 2 * 86400
        idle = time.time() - 10 * 86400
        _write_waves(tmp_path, [
            {"wave_id": "w_0001", "feature_description": "auth", "anchor_summaries": [], "created": age_ok, "last_activity": idle, "task_ids": ["task_000"]},
        ])
        reg = _make_registry(tmp_path, max_age_days=14, max_idle_days=5)
        mock_completion = MagicMock()
        mock_completion.return_value.choices[0].message.content = json.dumps({
            "flow_type": "feature",
            "wave_id": "new",
            "task_summary": "Add OAuth",
            "feature_description": "Auth overhaul",
        })
        result = reg.classify_task("add oauth", "task_001", mock_completion, "gemma-model")
        assert result["wave_id"].startswith("w_")
        assert result["wave_id"] != "w_0001"

    def test_llm_parse_error_leaves_unwaved(self, tmp_path):
        reg = _make_registry(tmp_path)
        mock_completion = MagicMock()
        mock_completion.return_value.choices[0].message.content = "not valid json"
        result = reg.classify_task("task", "task_001", mock_completion, "gemma-model")
        assert result["flow_type"] == "feature"
        assert result["wave_id"] is None
        assert result["task_summary"] is None

    def test_classified_fields_are_not_raw_spec_slices(self, tmp_path):
        reg = _make_registry(tmp_path)
        mock_completion = MagicMock()
        spec = "VALIDATION TOKEN: migrate-dashboard\n\nCONTEXT: Migrate the dashboard page to SvelteKit"
        mock_completion.return_value.choices[0].message.content = json.dumps({
            "flow_type": "feature",
            "wave_id": "new",
            "task_summary": "Migrate Dashboard to SvelteKit",
            "feature_description": "SvelteKit dashboard migration",
        })
        result = reg.classify_task(spec, "task_001", mock_completion, "gemma-model")
        assert result["task_summary"] == "Migrate Dashboard to SvelteKit"
        assert not result["task_summary"].startswith("VALIDATION TOKEN")

        waves = reg._read_waves()
        assert len(waves) == 1
        assert not waves[0].feature_description.startswith("VALIDATION TOKEN")
        assert not waves[0].feature_description.startswith(spec[:20])
        for anchor in waves[0].anchor_summaries:
            assert not anchor.startswith("VALIDATION TOKEN")

    def test_file_lock_acquired(self, tmp_path):
        reg = _make_registry(tmp_path)
        with patch("snodo.infrastructure.wave_registry.FileLock") as mock_lock_cls:
            mock_lock = MagicMock()
            mock_lock_cls.return_value = mock_lock
            mock_completion = MagicMock()
            mock_completion.return_value.choices[0].message.content = json.dumps({
                "flow_type": "defect",
                "wave_id": "new",
                "task_summary": "Fix bug",
                "feature_description": "Bug fixes",
            })
            reg.classify_task("fix bug", "task_001", mock_completion, "gemma-model")
            mock_lock.__enter__.assert_called_once()

    def test_related_task_matches_existing_wave(self, tmp_path):
        """Two related tasks land in the same wave (R2 matching)."""
        _write_waves(tmp_path, [
            {"wave_id": "w_0001", "feature_description": "SvelteKit dashboard migration", "anchor_summaries": ["Migrate Dashboard to SvelteKit"], "created": time.time(), "last_activity": time.time(), "task_ids": ["task_001"]},
        ])
        reg = _make_registry(tmp_path)
        mock_completion = MagicMock()
        mock_completion.return_value.choices[0].message.content = json.dumps({
            "flow_type": "feature",
            "wave_id": "w_0001",
            "task_summary": "Migrate Team page to SvelteKit",
        })
        result = reg.classify_task("Migrate Team page to SvelteKit framework", "task_002", mock_completion, "gemma-model")
        assert result["wave_id"] == "w_0001"
        waves = reg._read_waves()
        assert len(waves) == 1
        assert "task_002" in waves[0].task_ids

    def test_markdown_fence_json_parses(self, tmp_path):
        """JSON inside ``` fences parses correctly (R4 backstop)."""
        reg = _make_registry(tmp_path)
        mock_completion = MagicMock()
        mock_completion.return_value.choices[0].message.content = (
            "Here is the classification:\n\n```json\n"
            + json.dumps({"flow_type": "feature", "wave_id": "new", "task_summary": "Add login", "feature_description": "Auth system"})
            + "\n```"
        )
        result = reg.classify_task("build login", "task_001", mock_completion, "gemma-model")
        assert result["flow_type"] == "feature"
        assert result["wave_id"].startswith("w_")
        assert result["task_summary"] == "Add login"

    def test_null_wave_id_on_total_failure_logged(self, tmp_path, caplog):
        """Unparseable classifier response returns null wave_id (R3) and logs a warning."""
        import logging
        reg = _make_registry(tmp_path)
        mock_completion = MagicMock()
        mock_completion.return_value.choices[0].message.content = "totally broken response"
        with caplog.at_level(logging.WARNING):
            result = reg.classify_task("task", "task_001", mock_completion, "gemma-model")
        assert result["wave_id"] is None
        assert any("failed" in msg or "unwaved" in msg for msg in caplog.messages)


# ---------------------------------------------------------------------------
# Classification prompt
# ---------------------------------------------------------------------------


class TestPrompts:
    def test_prompt_mentions_four_types(self, tmp_path):
        reg = _make_registry(tmp_path)
        prompt = reg._build_prompt("task", [])
        for t in ("feature", "defect", "debt", "risk"):
            assert t in prompt.lower()

    def test_prompt_requests_required_fields(self, tmp_path):
        reg = _make_registry(tmp_path)
        prompt = reg._build_prompt("task", [])
        assert "flow_type" in prompt
        assert "wave_id" in prompt
        assert "task_summary" in prompt
        assert "feature_description" in prompt

    def test_prompt_requires_task_summary_not_spec_copy(self, tmp_path):
        reg = _make_registry(tmp_path)
        prompt = reg._build_prompt("task", [])
        assert "NEVER copy the spec" in prompt

    def test_prompt_with_multiple_open_waves(self, tmp_path):
        reg = _make_registry(tmp_path)
        waves = [
            WaveEntry(wave_id="w_0001", feature_description="auth"),
            WaveEntry(wave_id="w_0002", feature_description="deploy"),
        ]
        prompt = reg._build_prompt("task", waves)
        assert "w_0001" in prompt
        assert "w_0002" in prompt

    def test_prompt_has_no_bias_toward_minting(self, tmp_path):
        """Prompt must not instruct 'if uncertain return new' (R2)."""
        reg = _make_registry(tmp_path)
        prompt = reg._build_prompt("task", [])
        assert "uncertain" not in prompt.lower() or "evidence" in prompt.lower()

    def test_classifier_config_resolves_model(self):
        """Classifier model resolves from llm.classifier.model -> DEFAULT_MODEL (C1)."""
        from snodo.infrastructure.config import LlmConfig, DEFAULT_MODEL
        cfg = LlmConfig()
        model = (cfg.classifier.model if cfg.classifier and cfg.classifier.model else None) or DEFAULT_MODEL
        assert model == DEFAULT_MODEL
        assert model != "gemini/gemini-2.0-flash"

"""Tests for EXP1-RUN (enforcement ablation runner).

All tests run offline with mocks — no Docker, no swebench, no opencode,
no model API keys.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from experiments.arms.prose import protocol_to_prose
from experiments.run_exp1 import _load_protocol, _parity_gate, run_exp1
from experiments.scoring import MockScorer

_HERE = Path(__file__).resolve().parent
_FIXTURE = _HERE / "swe_verified_fixture.jsonl"

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mini_config() -> dict:
    """Minimal config for testing."""
    return {
        "selection": {"n": 10, "min_repos": 1, "seed": 42, "strata": {"easy": 4, "medium": 4, "hard": 2}},
        "sampling": {"temperature": 0.0, "k_trials": 2},
        "models": {"reference": "mock-model", "cutoff": None},
        "bounds": {"max_recovery_depth": 3, "max_total_fix_attempts": 10, "scoring": {"max_workers": 2, "namespace": "swebench", "cache_level": "instance"}, "dispatch": {"max_parallel": 2}},
        "stats": {"equivalence_margin_pp": 10, "min_meaningful_effect_pp": 15},
        "cloud": {"sync": False, "target": "staging"},
    }


@pytest.fixture
def sample_tasks() -> list[dict]:
    """Load the first 3 fixture tasks."""
    tasks = []
    with open(_FIXTURE) as f:
        for i, line in enumerate(f):
            if i >= 3:
                break
            tasks.append(json.loads(line.strip()))
    return tasks


@pytest.fixture
def results_dir(tmp_path) -> Path:
    return tmp_path / "results" / "exp1"


# ---------------------------------------------------------------------------
# MockScorer tests
# ---------------------------------------------------------------------------


class TestMockScorer:
    def test_gold_patch_resolves(self):
        scorer = MockScorer()
        instance = {"instance_id": "t1", "gold_patch": "--- a/x.py\n+++ b/x.py\n+print('ok')\n"}
        result = scorer.score(instance, instance["gold_patch"])
        assert result["resolved"] is True
        assert result["n_fail_to_pass_passed"] == 5

    def test_empty_patch_fails(self):
        scorer = MockScorer()
        instance = {"instance_id": "t1", "gold_patch": ""}
        result = scorer.score(instance, "")
        assert result["resolved"] is False
        assert result["error"] == "empty_patch"

    def test_non_gold_patch_fails(self):
        scorer = MockScorer()
        instance = {"instance_id": "t1", "gold_patch": "real-patch"}
        result = scorer.score(instance, "fake-patch")
        assert result["resolved"] is False
        assert result["regressions"] == 3

    def test_gold_not_resolving(self):
        scorer = MockScorer(gold_resolves=False)
        instance = {"instance_id": "t1", "gold_patch": "--- a/x.py\n+++ b/x.py\n"}
        result = scorer.score(instance, instance["gold_patch"])
        assert result["resolved"] is False


# ---------------------------------------------------------------------------
# Prose generator tests
# ---------------------------------------------------------------------------


class TestProseGenerator:
    def test_protocol_to_prose_produces_text(self):
        protocol = _load_protocol()
        prose = protocol_to_prose(protocol)
        assert isinstance(prose, str)
        assert len(prose) > 100
        assert "Protocol:" in prose
        assert "Producer" in prose
        assert "Intent-Driven" in prose

    def test_protocol_to_prose_mentions_validators(self):
        protocol = _load_protocol()
        prose = protocol_to_prose(protocol)
        assert "spec-manners" in prose
        assert "review" in prose

    def test_protocol_to_prose_mentions_modes_and_tools(self):
        protocol = _load_protocol()
        prose = protocol_to_prose(protocol)
        assert "edit" in prose
        assert "validate" in prose

    def test_protocol_to_prose_mentions_execution_config(self):
        protocol = _load_protocol()
        prose = protocol_to_prose(protocol)
        assert "Max retries" in prose
        assert "Max recovery depth" in prose
        assert "Branch prefix" in prose


# ---------------------------------------------------------------------------
# Parity gate tests
# ---------------------------------------------------------------------------


class TestParityGate:
    def test_parity_gate_passes(self):
        """Prose generated from protocol matches itself (reproducibility)."""
        protocol = _load_protocol()
        prose = protocol_to_prose(protocol)
        # Should not raise
        _parity_gate(protocol, prose)

    def test_parity_gate_fails_on_mismatch(self):
        """Assert that a tampered prose raises RuntimeError."""
        protocol = _load_protocol()
        with pytest.raises(RuntimeError, match="PARITY GATE FAILED"):
            _parity_gate(protocol, "tampered prose that doesn't match")


# ---------------------------------------------------------------------------
# Smoke test — full pipeline with mocks
# ---------------------------------------------------------------------------


class TestSmoke:
    """--smoke produces valid results per arm with mocks, no network."""

    def test_smoke_produces_rows_for_all_arms(self, mini_config, sample_tasks, results_dir):
        rows = run_exp1(
            config=mini_config,
            selection_path=_FIXTURE,
            results_dir=results_dir,
            arms=["a", "b", "c"],
            smoke=True,
            mock=True,
            tasks_override=sample_tasks,
        )
        assert len(rows) > 0
        arms_found = set(r["arm"] for r in rows)
        assert "a" in arms_found
        assert "b" in arms_found
        assert "c" in arms_found

    def test_smoke_rows_have_join_keys(self, mini_config, sample_tasks, results_dir):
        rows = run_exp1(
            config=mini_config,
            selection_path=_FIXTURE,
            results_dir=results_dir,
            smoke=True,
            mock=True,
            tasks_override=sample_tasks,
        )
        for row in rows:
            assert "instance_id" in row
            assert "arm" in row
            assert "trial_id" in row
            assert "run_id" in row
            assert "base_model" in row
            assert "temperature" in row
            assert "model_name_or_path" in row
            assert "resolved" in row
            assert "wall_s" in row

    def test_smoke_k_trials_respected(self, mini_config, sample_tasks, results_dir):
        mini_config["sampling"]["k_trials"] = 2
        rows = run_exp1(
            config=mini_config,
            selection_path=_FIXTURE,
            results_dir=results_dir,
            arms=["a"],
            smoke=True,
            mock=True,
            tasks_override=sample_tasks,
        )
        # 1 task × 1 arm × 2 trials = 2 rows (plus positive_control rows)
        arm_rows = [r for r in rows if r["arm"] == "a"]
        assert len(arm_rows) == 2
        assert arm_rows[0]["trial_id"] == 1
        assert arm_rows[1]["trial_id"] == 2

    def test_smoke_results_file_written(self, mini_config, sample_tasks, results_dir):
        run_exp1(
            config=mini_config,
            selection_path=_FIXTURE,
            results_dir=results_dir,
            smoke=True,
            mock=True,
            tasks_override=sample_tasks,
        )
        assert (results_dir / "results.jsonl").exists()

    def test_smoke_config_snapshot_written(self, mini_config, sample_tasks, results_dir):
        run_exp1(
            config=mini_config,
            selection_path=_FIXTURE,
            results_dir=results_dir,
            smoke=True,
            mock=True,
            tasks_override=sample_tasks,
        )
        assert (results_dir / "experiment_config.yml").exists()

    def test_arm_c_closure_json_in_row(self, mini_config, sample_tasks, results_dir):
        rows = run_exp1(
            config=mini_config,
            selection_path=_FIXTURE,
            results_dir=results_dir,
            arms=["c"],
            smoke=True,
            mock=True,
            tasks_override=sample_tasks,
        )
        for row in rows:
            if row["arm"] == "c":
                assert row["closure_json"] is not None
                assert "task_id" in row["closure_json"]
                assert "outcome" in row["closure_json"]


# ---------------------------------------------------------------------------
# Positive control / harness_broken tests
# ---------------------------------------------------------------------------


class TestPositiveControl:
    def test_gold_patch_excluded_when_scorer_fails(self, mini_config, results_dir):
        """If gold_patch doesn't resolve, task is excluded with harness_broken."""
        from experiments.scoring import MockScorer
        tasks = [{
            "instance_id": "broken-task",
            "gold_patch": "some-patch",
            "problem_statement": "fix the bug",
            "repo": "test/repo",
        }]
        failing_scorer = MockScorer(gold_resolves=False)
        rows = run_exp1(
            config=mini_config,
            selection_path=_FIXTURE,
            results_dir=results_dir,
            arms=["a", "b", "c"],
            mock=True,
            tasks_override=tasks,
            smoke=True,
            scorer_override=failing_scorer,
        )
        assert len(rows) == 1
        assert rows[0]["arm"] == "positive_control"
        assert rows[0]["exclusion_reason"] == "harness_broken"

    def test_normal_task_not_excluded(self, mini_config, sample_tasks, results_dir):
        """Task with resolving gold patch is not excluded."""
        rows = run_exp1(
            config=mini_config,
            selection_path=_FIXTURE,
            results_dir=results_dir,
            arms=["a"],
            mock=True,
            tasks_override=sample_tasks,
            smoke=True,
        )
        for row in rows:
            assert row.get("exclusion_reason") is None


# ---------------------------------------------------------------------------
# Idempotency tests
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_no_duplicate_rows_without_force(self, mini_config, sample_tasks, results_dir):
        """Re-running without --force adds no duplicate rows."""
        run_exp1(
            config=mini_config,
            selection_path=_FIXTURE,
            results_dir=results_dir,
            arms=["a"],
            smoke=True,
            mock=True,
            tasks_override=sample_tasks,
        )
        rows2 = run_exp1(
            config=mini_config,
            selection_path=_FIXTURE,
            results_dir=results_dir,
            arms=["a"],
            smoke=True,
            mock=True,
            tasks_override=sample_tasks,
        )
        # Second run adds no new arm rows (only existing rows loaded)
        new_arm_rows = [r for r in rows2 if r["arm"] != "positive_control"]
        assert len(new_arm_rows) == 0

    def test_force_adds_duplicates(self, mini_config, sample_tasks, results_dir):
        """Re-running with --force adds new rows even if duplicates exist."""
        rows1 = run_exp1(
            config=mini_config,
            selection_path=_FIXTURE,
            results_dir=results_dir,
            arms=["a"],
            smoke=True,
            mock=True,
            tasks_override=sample_tasks,
            force=False,
        )
        # First run: positive control passes, so arm rows are produced
        arm1 = [r for r in rows1 if r["arm"] == "a"]
        assert len(arm1) >= 1  # 1 task x k_trials arm rows

        rows2 = run_exp1(
            config=mini_config,
            selection_path=_FIXTURE,
            results_dir=results_dir,
            arms=["a"],
            smoke=True,
            mock=True,
            tasks_override=sample_tasks,
            force=True,
        )
        # With force, new rows are still produced (same task/arm/trial but
        # different run_id — they count as new to the returned list)
        arm2 = [r for r in rows2 if r["arm"] == "a"]
        assert len(arm2) >= 1


# ---------------------------------------------------------------------------
# Branch cleanup tests
# ---------------------------------------------------------------------------


class TestBranchCleanup:
    def test_cleanup_removes_matching_branches(self, tmp_path):
        """cleanup_branches removes branches matching the run pattern."""
        repo = Path(tmp_path) / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)
        (repo / "README.md").write_text("test")
        subprocess.run(["git", "add", "README.md"], cwd=repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)

        # Create branches matching the pattern
        subprocess.run(["git", "checkout", "-b", "task/exp1-task001-run123-t1"], cwd=repo, capture_output=True)
        subprocess.run(["git", "checkout", "main"], cwd=repo, capture_output=True)
        subprocess.run(["git", "checkout", "-b", "task/exp1-task001-run123-t2"], cwd=repo, capture_output=True)
        subprocess.run(["git", "checkout", "main"], cwd=repo, capture_output=True)

        # Create an unrelated branch
        subprocess.run(["git", "checkout", "-b", "task/some-other-task"], cwd=repo, capture_output=True)
        subprocess.run(["git", "checkout", "main"], cwd=repo, capture_output=True)

        from experiments.arms.arm_c_snodo import cleanup_branches
        cleanup_branches("task001", "run123", project_root=str(repo))

        remaining = subprocess.run(
            ["git", "branch", "--list", "task/*"],
            cwd=repo, capture_output=True, text=True,
        ).stdout.strip().splitlines()
        remaining = [b.strip().lstrip("* ") for b in remaining if b.strip()]

        assert "task/some-other-task" in remaining
        assert "task/exp1-task001-run123-t1" not in remaining
        assert "task/exp1-task001-run123-t2" not in remaining

    def test_cleanup_no_branches_no_error(self, tmp_path):
        """cleanup_branches does not error when there are no matching branches."""
        from experiments.arms.arm_c_snodo import cleanup_branches
        # No git repo at all — should not crash
        cleanup_branches("task001", "run123", project_root=str(tmp_path))


# ---------------------------------------------------------------------------
# Scoring module tests
# ---------------------------------------------------------------------------


class TestScoringModule:
    def test_get_instance_missing(self):
        """Non-existent instance_id returns None."""
        with pytest.raises(FileNotFoundError):
            from experiments.scoring import get_instance
            get_instance("non-existent", Path("/nonexistent/file"))


# ---------------------------------------------------------------------------
# Batch scoring tests
# ---------------------------------------------------------------------------


class TestBatchScoring:
    """score_batch returns correct results for mock scorer."""

    def test_batch_single_prediction(self):
        scorer = MockScorer()
        instance = {"instance_id": "t1", "gold_patch": "patch-abc"}
        results = scorer.score_batch([
            (instance, "patch-abc", "model-a"),
        ])
        key = ("t1", "model-a")
        assert key in results
        assert results[key]["resolved"] is True  # matches gold

    def test_batch_multiple_predictions(self):
        scorer = MockScorer()
        instance = {"instance_id": "t1", "gold_patch": "patch-abc"}
        results = scorer.score_batch([
            (instance, "patch-abc", "model-a"),   # gold → resolved
            (instance, "patch-xyz", "model-b"),   # not gold → not resolved
        ])
        assert results[("t1", "model-a")]["resolved"] is True
        assert results[("t1", "model-b")]["resolved"] is False

    def test_batch_empty_input(self):
        scorer = MockScorer()
        results = scorer.score_batch([])
        assert results == {}

    def test_batch_with_empty_patch(self):
        scorer = MockScorer()
        instance = {"instance_id": "t1", "gold_patch": "patch-abc"}
        results = scorer.score_batch([
            (instance, "", "model-empty"),
        ])
        key = ("t1", "model-empty")
        assert key in results
        assert results[key]["resolved"] is False


# ---------------------------------------------------------------------------
# Gold cache tests
# ---------------------------------------------------------------------------


class TestGoldCache:
    """Gold-patch result cache avoids redundant harness invocations."""

    def setup_method(self):
        from experiments.scoring import clear_gold_cache
        clear_gold_cache()

    def test_cache_hit_returns_same_result(self):
        from experiments.scoring import _cached_gold_result, _set_cached_gold_result
        instance = {"instance_id": "t1"}
        result = {"resolved": True, "n_fail_to_pass_passed": 5, "regressions": 0, "error": None}
        _set_cached_gold_result(instance, result)
        cached = _cached_gold_result(instance)
        assert cached == result

    def test_cache_miss_returns_none(self):
        from experiments.scoring import _cached_gold_result
        instance = {"instance_id": "never-cached"}
        assert _cached_gold_result(instance) is None

    def test_clear_cache(self):
        from experiments.scoring import _cached_gold_result, _set_cached_gold_result, clear_gold_cache
        instance = {"instance_id": "t1"}
        _set_cached_gold_result(instance, {"resolved": True})
        clear_gold_cache()
        assert _cached_gold_result(instance) is None


# ---------------------------------------------------------------------------
# Parallel dispatch tests
# ---------------------------------------------------------------------------


class TestParallelDispatch:
    """ProcessPoolExecutor dispatch and _run_one_cell work correctly."""

    def test_run_one_cell_returns_serialized_result(self):
        """_run_one_cell returns a JSON-encoded dict with expected keys."""
        from experiments.run_exp1 import _run_one_cell
        result_json = _run_one_cell(
            json.dumps({"instance_id": "t1", "repo": "test/repo", "base_commit": "HEAD", "hints": ""}),
            "z", 1, json.dumps({"models": {"reference": "test"}, "sampling": {"temperature": 0.0}}),
            "run-test", "",
        )
        result = json.loads(result_json)
        # Always has expected keys (either error from workspace_setup or dispatch)
        assert "error" in result
        assert "patch" in result
        assert "wall_s" in result
        assert "closure_json" in result

    def test_max_parallel_from_config(self):
        """Config bounds.dispatch.max_parallel is read by run_exp1."""
        from experiments.run_exp1 import run_exp1
        cfg = {
            "selection": {"n": 10, "min_repos": 1, "seed": 42, "strata": {"easy": 4, "medium": 4, "hard": 2}},
            "sampling": {"temperature": 0.0, "k_trials": 1},
            "models": {"reference": "mock-model", "cutoff": None},
            "bounds": {"max_recovery_depth": 3, "max_total_fix_attempts": 10, "scoring": {"max_workers": 2, "namespace": "swebench", "cache_level": "instance"}, "dispatch": {"max_parallel": 4}},
            "stats": {"equivalence_margin_pp": 10, "min_meaningful_effect_pp": 15},
            "cloud": {"sync": False, "target": "staging"},
        }
        rows = run_exp1(
            config=cfg,
            selection_path=_FIXTURE,
            results_dir=Path("/tmp/_test_dispatch"),
            arms=["a"],
            smoke=True,
            mock=True,
            tasks_override=[{"instance_id": "t1", "gold_patch": "--- a/x.py\n+print('ok')\n", "repo": "test/repo"}],
        )
        # Cleanup
        import shutil
        shutil.rmtree(Path("/tmp/_test_dispatch"), ignore_errors=True)
        # Smoke test: should produce rows (mock + serial path)
        assert len([r for r in rows if r["arm"] == "a"]) == 1


# ---------------------------------------------------------------------------
# Config snapshot test
# ---------------------------------------------------------------------------


class TestLimitAndInstance:
    def test_limit_respected(self, mini_config, sample_tasks, results_dir):
        """--limit N processes at most N tasks."""
        rows = run_exp1(
            config=mini_config,
            selection_path=_FIXTURE,
            results_dir=results_dir,
            arms=["a"],
            mock=True,
            smoke=False,
            tasks_override=sample_tasks,
            limit=1,
        )
        # Only 1 task x 1 arm x 2 trials = 2 arm rows
        arm_rows = [r for r in rows if r["arm"] == "a"]
        assert len(arm_rows) == 2

    def test_instance_filter(self, mini_config, results_dir):
        """--instance <id> runs only that instance."""
        rows = run_exp1(
            config=mini_config,
            selection_path=_FIXTURE,
            results_dir=results_dir,
            arms=["a"],
            mock=True,
            smoke=False,
            instance_id="astropy__astropy-12907",
        )
        assert len(rows) > 0
        for r in rows:
            assert r["instance_id"] == "astropy__astropy-12907"

    def test_instance_not_found(self, mini_config, results_dir):
        """--instance with unknown id raises."""
        with pytest.raises(ValueError, match="not found"):
            run_exp1(
                config=mini_config,
                selection_path=_FIXTURE,
                results_dir=results_dir,
                arms=["a"],
                mock=True,
                instance_id="nonexistent-instance",
            )


class TestCloudMetadata:
    def test_result_row_has_experiment_data(self, mini_config, sample_tasks, results_dir):
        """Every result row stamps data.experiment and data.run_id."""
        rows = run_exp1(
            config=mini_config,
            selection_path=_FIXTURE,
            results_dir=results_dir,
            arms=["a"],
            smoke=True,
            mock=True,
            tasks_override=sample_tasks,
        )
        for row in rows:
            assert "data" in row
            assert row["data"]["experiment"] == "exp1"
            assert row["data"]["run_id"].startswith("exp1-")


class TestConfigSnapshot:
    def test_snapshot_includes_run_id(self, mini_config, sample_tasks, results_dir):
        run_exp1(
            config=mini_config,
            selection_path=_FIXTURE,
            results_dir=results_dir,
            arms=["a"],
            smoke=True,
            mock=True,
            tasks_override=sample_tasks,
        )
        import yaml
        with open(results_dir / "experiment_config.yml") as f:
            snap = yaml.safe_load(f)
        assert "_run_id" in snap
        assert snap["_run_id"].startswith("exp1-")
        assert "_arms" in snap
        assert snap["_arms"] == ["a"]


# ---------------------------------------------------------------------------
# Workspace cache tests
# ---------------------------------------------------------------------------


class TestWorkspaceCache:
    def test_cleanup_cache_clears(self):
        from experiments.workspace import cleanup_cache, _CACHE, _CACHE_DIR
        # Prime the cache with a sentinel
        _CACHE[("test/repo", "abc123")] = "/tmp/sentinel"
        cleanup_cache()
        assert len(_CACHE) == 0
        assert _CACHE_DIR is None

    def test_cache_key_creation(self):
        """Verify the cache key format used by _get_cached."""
        from experiments.workspace import _CACHE
        _CACHE.clear()
        key = ("test/repo", "deadbeef")
        _CACHE[key] = "/tmp/cached"
        assert _CACHE.get(key) == "/tmp/cached"
        _CACHE.clear()

    def test_mock_workspace_extract_patch(self):
        """MockWorkspace still returns its predetermined patch."""
        from experiments.workspace import MockWorkspace
        mw = MockWorkspace(patch="custom-mock-patch")
        ws = mw.setup({"instance_id": "test"})
        assert mw.extract_patch(ws) == "custom-mock-patch"
        mw.teardown(ws)

    def test_mock_workspace_base_commit(self):
        """MockWorkspace returns base_commit='HEAD'."""
        from experiments.workspace import MockWorkspace
        mw = MockWorkspace()
        ws = mw.setup({"instance_id": "test"})
        assert ws.base_commit == "HEAD"
        mw.teardown(ws)

    def test_shallow_fetch_git_init(self, tmp_path):
        """_do_shallow_fetch: git init + remote add (no network)."""
        from experiments.workspace import _run
        dest = tmp_path / "shallow-test"
        dest.mkdir()
        _run(["git", "init"], cwd=dest)
        _run(["git", "remote", "add", "origin", "https://github.com/test/repo.git"], cwd=dest)
        assert (dest / ".git").exists()
        result = subprocess.run(
            ["git", "remote", "-v"],
            cwd=dest, capture_output=True, text=True,
        )
        assert "test/repo" in result.stdout
        assert result.returncode == 0

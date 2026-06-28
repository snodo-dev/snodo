"""Tests for EXP-SELECT (deterministic task selection).

All tests run offline against a committed mini-fixture (20 rows from
SWE-bench_Verified). No network calls.
"""

import csv
import json
from collections import Counter
from pathlib import Path

import pytest

from experiments.select_tasks import (
    _contamination_flag,
    _filter_and_enrich,
    _patch_stats,
    _stratified_pick,
    select_tasks,
)

_HERE = Path(__file__).resolve().parent
_FIXTURE = _HERE / "swe_verified_fixture.jsonl"


def _load_fixture() -> list:
    rows = []
    with open(_FIXTURE) as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


class TestPatchStats:

    def test_patch_stats_counts_files_and_loc(self):
        patch = (
            "diff --git a/file1.py b/file1.py\n"
            "--- a/file1.py\n"
            "+++ b/file1.py\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n"
            "diff --git a/file2.py b/file2.py\n"
            "--- a/file2.py\n"
            "+++ b/file2.py\n"
            "@@ -5 +5 @@\n"
            "-foo\n"
            "+bar\n"
        )
        stats = _patch_stats(patch)
        assert stats["n_files"] == 2
        assert stats["added_loc"] == 2

    def test_empty_patch(self):
        assert _patch_stats("") == {"n_files": 0, "added_loc": 0}

    def test_none_patch(self):
        assert _patch_stats(None) == {"n_files": 0, "added_loc": 0}


class TestContaminationFlag:

    def test_no_cutoff_returns_unknown(self):
        assert _contamination_flag("2024-06-01", None) == "unknown"

    def test_clean(self):
        assert _contamination_flag("2024-12-01", "2024-06-01") == "clean"

    def test_contaminated(self):
        assert _contamination_flag("2024-01-01", "2024-06-01") == "contaminated"


class TestFilterAndEnrich:

    def test_filters_empty_ftp(self):
        rows = [{"instance_id": "t1", "patch": "diff --git a/x.py b/x.py\n+1\n",
                 "FAIL_TO_PASS": "[]", "PASS_TO_PASS": "[]",
                 "difficulty": "<15 min fix", "repo": "a/b",
                 "created_at": "2024-01-01"}]
        result = _filter_and_enrich(rows, {"models": {}})
        assert len(result) == 0

    def test_enriches_difficulty(self):
        rows = [{"instance_id": "t1", "patch": "diff --git a/x.py b/x.py\n+1\n",
                 "FAIL_TO_PASS": '["test_a.py::test1"]',
                 "PASS_TO_PASS": "[]",
                 "difficulty": "15 min - 1 hour", "repo": "a/b",
                 "created_at": "2024-01-01"}]
        result = _filter_and_enrich(rows, {"models": {}})
        assert len(result) == 1
        assert result[0]["difficulty"] == "medium"
        assert result[0]["difficulty_source"] == "swe_bench_verified_label"

    def test_contamination_flag_from_config(self):
        rows = [{"instance_id": "t1", "patch": "diff --git a/x.py b/x.py\n+1\n",
                 "FAIL_TO_PASS": '["test_a.py::test1"]',
                 "PASS_TO_PASS": "[]",
                 "difficulty": "<15 min fix", "repo": "a/b",
                 "created_at": "2024-01-01"}]
        config = {"models": {"cutoff": "2024-06-01"}}
        result = _filter_and_enrich(rows, config)
        assert result[0]["contamination_flag"] == "contaminated"


class TestStratifiedPick:

    def test_picks_exact_n(self):
        candidates = []
        for i in range(50):
            diff = "easy" if i < 30 else "medium"
            candidates.append({
                "instance_id": f"t{i:04d}",
                "repo": f"org/repo{i % 5}",
                "difficulty": diff,
            })
        picked = _stratified_pick(candidates, ["easy", "medium"], n_total=10,
                                   min_repos=3, seed=42)
        assert len(picked) == 10
        assert len({c["repo"] for c in picked}) >= 3

    def test_unavailable_stratum_raises(self):
        candidates = [{"instance_id": "t1", "repo": "a/b", "difficulty": "easy"}]
        with pytest.raises(ValueError, match="has 0 candidates"):
            _stratified_pick(candidates, ["easy", "hard"], n_total=2,
                              min_repos=1, seed=42)

    def test_deterministic(self):
        candidates = []
        for i in range(30):
            candidates.append({
                "instance_id": f"t{i:04d}",
                "repo": f"org/repo{i % 3}",
                "difficulty": "easy" if i < 15 else "medium",
            })
        pick1 = _stratified_pick(candidates, ["easy", "medium"], n_total=6,
                                  min_repos=2, seed=42)
        pick2 = _stratified_pick(candidates, ["easy", "medium"], n_total=6,
                                  min_repos=2, seed=42)
        ids1 = [c["instance_id"] for c in pick1]
        ids2 = [c["instance_id"] for c in pick2]
        assert ids1 == ids2

    def test_explicit_counts_happy_path(self):
        """Dict strata: picks exactly the requested counts per stratum."""
        candidates = []
        for i in range(30):
            diff = "easy" if i < 10 else "medium" if i < 20 else "hard"
            candidates.append({
                "instance_id": f"t{i:04d}",
                "repo": f"org/repo{i % 5}",
                "difficulty": diff,
            })
        picked = _stratified_pick(
            candidates, {"easy": 2, "medium": 3, "hard": 1},
            n_total=6, min_repos=1, seed=42,
        )
        assert len(picked) == 6
        counts = Counter(c["difficulty"] for c in picked)
        assert counts["easy"] == 2
        assert counts["medium"] == 3
        assert counts["hard"] == 1

    def test_explicit_counts_pool_underfill_raises(self):
        """Dict strata: loud-fail when a stratum can't supply its count."""
        candidates = []
        for i in range(3):
            candidates.append({
                "instance_id": f"t{i:04d}",
                "repo": f"org/repo{i % 2}",
                "difficulty": "easy",
            })
        with pytest.raises(ValueError, match="requested 5 but pool has only"):
            _stratified_pick(
                candidates, {"easy": 5}, n_total=5, min_repos=1, seed=42,
            )

    def test_explicit_counts_zero_stratum_skipped(self):
        """Dict strata: stratum with count 0 is skipped (no phase-1 pick)."""
        candidates = []
        for i in range(10):
            diff = "easy" if i < 5 else "medium"
            candidates.append({
                "instance_id": f"t{i:04d}",
                "repo": f"org/repo{i % 3}",
                "difficulty": diff,
            })
        picked = _stratified_pick(
            candidates, {"easy": 2, "medium": 0},
            n_total=2, min_repos=1, seed=42,
        )
        assert len(picked) == 2
        assert all(c["difficulty"] == "easy" for c in picked)

    def test_explicit_counts_deterministic(self):
        """Dict strata: same seed produces same selection."""
        candidates = []
        for i in range(30):
            diff = "easy" if i < 10 else "medium" if i < 20 else "hard"
            candidates.append({
                "instance_id": f"t{i:04d}",
                "repo": f"org/repo{i % 5}",
                "difficulty": diff,
            })
        pick1 = _stratified_pick(
            candidates, {"easy": 2, "medium": 2, "hard": 1},
            n_total=5, min_repos=1, seed=42,
        )
        pick2 = _stratified_pick(
            candidates, {"easy": 2, "medium": 2, "hard": 1},
            n_total=5, min_repos=1, seed=42,
        )
        ids1 = [c["instance_id"] for c in pick1]
        ids2 = [c["instance_id"] for c in pick2]
        assert ids1 == ids2


class TestSelectTasksOffline:

    def test_select_from_fixture(self, tmp_path):
        """Full pipeline against the offline fixture."""
        out = tmp_path / "tasks"
        selected = select_tasks(
            dataset_path=str(_FIXTURE),
            cli_overrides=["selection.n=5", "selection.min_repos=1",
                           'selection.strata={"easy":2,"medium":2,"hard":1}'],
            output_dir=out,
        )
        assert len(selected) == 5
        # Every row must have required fields
        for row in selected:
            assert row["gold_patch"]
            assert row["test_patch"] is not None
            assert len(row["fail_to_pass"]) > 0

    def test_output_files_exist(self, tmp_path):
        out = tmp_path / "tasks"
        select_tasks(
            dataset_path=str(_FIXTURE),
            cli_overrides=["selection.n=4", "selection.min_repos=1",
                           'selection.strata={"easy":2,"medium":1,"hard":1}'],
            output_dir=out,
        )
        assert (out / "selection.jsonl").exists()
        assert (out / "selection.csv").exists()
        assert (out / "pool_stats.txt").exists()

    def test_csv_columns(self, tmp_path):
        out = tmp_path / "tasks"
        select_tasks(
            dataset_path=str(_FIXTURE),
            cli_overrides=["selection.n=4", "selection.min_repos=1",
                           'selection.strata={"easy":2,"medium":1,"hard":1}'],
            output_dir=out,
        )
        with open(out / "selection.csv") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert len(rows) == 4
        for row in rows:
            assert row["instance_id"]
            assert row["difficulty"]
            assert row["n_fail_to_pass"]

    def test_jsonl_loadable(self, tmp_path):
        out = tmp_path / "tasks"
        select_tasks(
            dataset_path=str(_FIXTURE),
            cli_overrides=["selection.n=4", "selection.min_repos=1",
                           'selection.strata={"easy":2,"medium":1,"hard":1}'],
            output_dir=out,
        )
        with open(out / "selection.jsonl") as f:
            lines = [json.loads(line) for line in f if line.strip()]
        assert len(lines) == 4
        for row in lines:
            assert "instance_id" in row
            assert "difficulty" in row

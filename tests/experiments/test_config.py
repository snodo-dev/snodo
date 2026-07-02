"""Tests for experiment configuration (EXP-CONFIG).

Covers: loading, validation, CLI overrides, snapshot provenance,
type coercion, and out-of-range rejection.
"""

import json
from pathlib import Path

import yaml
import pytest

from experiments.config import load_config, write_snapshot, format_config


_VALID_CONFIG = {
    "selection": {
        "dataset": "swe_bench_verified",
        "n": 10,
        "min_repos": 5,
        "strata": ["easy", "medium", "hard"],
        "seed": 42,
    },
    "sampling": {
        "temperature": 0.0,
        "k_trials": 3,
    },
    "bounds": {
        "max_recovery_depth": 3,
        "max_total_fix_attempts": 10,
        "scoring": {"max_workers": 4},
    },
    "models": {
        "expensive": ["claude-sonnet-4", "gpt-4o"],
        "commodity": ["deepseek/deepseek-v4-flash", "gpt-4o-mini"],
        "reference": "claude-sonnet-4",
    },
    "stats": {
        "primary_metric": "pass_at_1",
        "equivalence_margin_pp": 10,
        "min_meaningful_effect_pp": 15,
    },
}


@pytest.fixture
def valid_config_path(tmp_path):
    path = tmp_path / "config.yml"
    with open(path, "w") as f:
        yaml.dump(_VALID_CONFIG, f, default_flow_style=False)
    return path


class TestLoadConfig:

    def test_loads_valid_config(self, valid_config_path):
        cfg = load_config(path=valid_config_path)
        assert cfg["selection"]["n"] == 10
        assert cfg["sampling"]["temperature"] == 0.0
        assert cfg["bounds"]["max_recovery_depth"] == 3
        assert cfg["stats"]["equivalence_margin_pp"] == 10

    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError, match="Experiment config not found"):
            load_config(path=Path("/nonexistent/config.yml"))

    def test_missing_key_raises(self, valid_config_path, tmp_path):
        import copy
        bad = copy.deepcopy(_VALID_CONFIG)
        del bad["selection"]["n"]
        path = tmp_path / "bad_config.yml"
        with open(path, "w") as f:
            yaml.dump(bad, f)
        with pytest.raises(ValueError, match="Missing required config key"):
            load_config(path=path)

    def test_out_of_range_low(self, valid_config_path, tmp_path):
        import copy
        bad = copy.deepcopy(_VALID_CONFIG)
        bad["selection"]["n"] = 0  # below min 1
        path = tmp_path / "bad_n.yml"
        with open(path, "w") as f:
            yaml.dump(bad, f)
        with pytest.raises(ValueError, match="below minimum"):
            load_config(path=path)

    def test_out_of_range_high(self, valid_config_path, tmp_path):
        import copy
        bad = copy.deepcopy(_VALID_CONFIG)
        bad["bounds"]["max_recovery_depth"] = 21  # above max 20
        path = tmp_path / "bad_depth.yml"
        with open(path, "w") as f:
            yaml.dump(bad, f)
        with pytest.raises(ValueError, match="exceeds maximum"):
            load_config(path=path)

    def test_wrong_type_raises(self, valid_config_path, tmp_path):
        import copy
        bad = copy.deepcopy(_VALID_CONFIG)
        bad["selection"]["n"] = "not_a_number"
        path = tmp_path / "bad_type.yml"
        with open(path, "w") as f:
            yaml.dump(bad, f)
        with pytest.raises(ValueError, match="expected int"):
            load_config(path=path)


class TestCLIOverrides:

    def test_override_n(self, valid_config_path):
        cfg = load_config(path=valid_config_path, cli_overrides=["selection.n=30"])
        assert cfg["selection"]["n"] == 30

    def test_override_temperature(self, valid_config_path):
        cfg = load_config(path=valid_config_path, cli_overrides=["sampling.temperature=0.5"])
        assert cfg["sampling"]["temperature"] == 0.5

    def test_override_preserves_other_values(self, valid_config_path):
        cfg = load_config(path=valid_config_path, cli_overrides=["selection.n=20"])
        assert cfg["selection"]["n"] == 20
        assert cfg["selection"]["seed"] == 42  # unchanged

    def test_multiple_overrides(self, valid_config_path):
        cfg = load_config(
            path=valid_config_path,
            cli_overrides=["selection.n=15", "sampling.k_trials=5"],
        )
        assert cfg["selection"]["n"] == 15
        assert cfg["sampling"]["k_trials"] == 5

    def test_invalid_override_format(self, valid_config_path):
        with pytest.raises(ValueError, match="Invalid --set format"):
            load_config(path=valid_config_path, cli_overrides=["badformat"])


class TestSnapshot:

    def test_snapshot_written(self, valid_config_path, tmp_path):
        cfg = load_config(path=valid_config_path)
        run_dir = tmp_path / "run_001"
        dest = write_snapshot(run_dir, cfg)
        assert dest.exists()
        with open(dest) as f:
            snap = yaml.safe_load(f)
        assert snap["selection"]["n"] == 10

    def test_snapshot_matches_override(self, valid_config_path, tmp_path):
        cfg = load_config(path=valid_config_path, cli_overrides=["selection.n=42"])
        run_dir = tmp_path / "run_002"
        dest = write_snapshot(run_dir, cfg)
        with open(dest) as f:
            snap = yaml.safe_load(f)
        assert snap["selection"]["n"] == 42
        assert snap["selection"]["seed"] == 42  # default preserved

    def test_snapshot_does_not_modify_original(self, valid_config_path, tmp_path):
        cfg = load_config(path=valid_config_path)
        run_dir = tmp_path / "run_003"
        write_snapshot(run_dir, cfg)
        cfg["selection"]["n"] = 999  # mutate post-snapshot
        with open(run_dir / "experiment_config.yml") as f:
            snap = yaml.safe_load(f)
        assert snap["selection"]["n"] == 10  # snapshot frozen


class TestFormatConfig:

    def test_format_config(self, valid_config_path):
        cfg = load_config(path=valid_config_path)
        formatted = format_config(cfg)
        parsed = json.loads(formatted)
        assert parsed["selection"]["n"] == 10

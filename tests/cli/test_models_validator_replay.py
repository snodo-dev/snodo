import json

import pytest

from snodo.cli.commands.models_validator_replay import resolve_validator_replay_input


def _fixture(tmp_path, phase="post_execute"):
    root = tmp_path
    plan_dir = root / ".snodo" / "plans" / "p"
    (plan_dir / "wave_1").mkdir(parents=True)
    (plan_dir / "plan.yml").write_text("waves:\n  - id: 1\n    tasks: [t]\n", encoding="utf-8")
    (plan_dir / "wave_1" / "t_task.md").write_text("spec text", encoding="utf-8")
    snodo = root / ".snodo"
    (snodo / "protocol.yml").write_text(f"validators:\n  - validator_id: v\n    evaluation_phase: {phase}\n", encoding="utf-8")
    baseline_dir = snodo / "baselines" / "p" / "t"
    baseline_dir.mkdir(parents=True)
    (baseline_dir / "baseline.json").write_text(json.dumps({
        "validator_results": [{"validator_id": "v", "severity": "warn", "justification": "reason", "elapsed_ms": 12, "token_usage": {"input": 3}}],
        "change_size": {"base_sha": "base", "head_sha": "head"},
    }), encoding="utf-8")
    return root


def test_replay_input_for_post_execute_validator_uses_head_commit(tmp_path):
    result = resolve_validator_replay_input(_fixture(tmp_path), "p", "t", "v")
    assert (result.spec, result.phase, result.head_commit) == ("spec text", "post_execute", "head")
    assert result.result["severity"] == "warn"


def test_replay_input_for_pre_execute_validator_uses_base_commit(tmp_path):
    result = resolve_validator_replay_input(_fixture(tmp_path, "pre_execute"), "p", "t", "v")
    assert result.head_commit == result.base_commit == "base"


def test_replay_input_unknown_validator_lists_recorded_ids(tmp_path):
    with pytest.raises(ValueError, match="v"):
        resolve_validator_replay_input(_fixture(tmp_path), "p", "t", "missing")


def test_replay_input_missing_baseline_is_a_clear_error(tmp_path):
    with pytest.raises(ValueError, match="baseline"):
        resolve_validator_replay_input(tmp_path, "p", "t", "v")

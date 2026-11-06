"""Offline tests for SWE-bench compatibility and experiment cost reporting."""

import json
from types import SimpleNamespace

from experiments import scoring
from experiments.arms import arm_a_opencode, arm_c_snodo


def test_dataset_row_adds_swebench_image():
    row = {"instance_id": "Org__Repo-123"}

    result = scoring._add_swebench_image(row, "5.0.2")

    assert result["image"] == "swebench/sweb.eval.org__repo-123:latest"
    assert "image" not in row
    assert scoring._add_swebench_image(row, "4.2.1") is row


def test_arm_c_cost_through_run_callsite(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SNODO_RUN_ID", "")
    monkeypatch.setenv("SNODO_TRIAL_ID", "")
    task = {"instance_id": "sample", "problem_statement": "spec"}
    run_id = "cost-test"
    task_id = f"exp1-sample-{run_id}-t1"
    project = tmp_path / "workspace"
    state_path = project / ".snodo" / "tasks" / task_id / "state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        json.dumps(
            {
                "usage": [
                    {
                        "role": "coder",
                        "model": "test",
                        "outcome": "succeeded",
                        "input_tokens": 10,
                        "output_tokens": 5,
                        "cost": 0.025,
                        "cost_source": "provider",
                        "duration_ms": 100,
                    }
                ]
            }
        )
    )
    workspace = SimpleNamespace(path=project)
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setattr(arm_c_snodo, "_snodo_config_with_model", lambda *_: config_dir)
    monkeypatch.setattr(
        arm_c_snodo,
        "build_protocol_graph",
        lambda *a, **k: SimpleNamespace(compile=lambda: "compiled"),
    )
    monkeypatch.setattr(
        arm_c_snodo,
        "run_to_closure",
        lambda *a, **k: (
            {},
            SimpleNamespace(
                task_id=task_id,
                depth=0,
                outcome="done",
                spawned_subtasks=[],
                attempts_used=0,
                subtasks=[],
            ),
        ),
    )
    monkeypatch.setattr(arm_c_snodo, "extract_patch", lambda *_: "patch")
    monkeypatch.setattr(arm_c_snodo, "_do_cloud_sync", lambda *a: None)

    def call_run():
        return arm_c_snodo.run(
            task,
            {"models": {"reference": "test"}},
            run_id,
            1,
            protocol=object(),
            workspace=workspace,
        )

    with_cost = call_run()
    assert with_cost["cost_usd"] == 0.025
    assert with_cost["cost_source"] == "snodo_usage"

    state_path.write_text(json.dumps({"usage": []}))
    without_cost = call_run()
    assert without_cost["cost_usd"] is None
    assert without_cost["cost_source"] == "unavailable"


def test_arm_a_parses_usage_samples():
    samples = [
        (
            '{"usage":{"input_tokens":12,"output_tokens":8,"cost_usd":0.04}}',
            (0.04, {"input_tokens": 12, "output_tokens": 8}),
        ),
        (
            '{"usage":{"input_tokens":12,"output_tokens":8}}',
            (None, {"input_tokens": 12, "output_tokens": 8}),
        ),
        ("ordinary output", (None, None)),
    ]
    for sample, expected in samples:
        assert arm_a_opencode._parse_usage(sample) == expected


def test_swebench_missing_returns_error(monkeypatch):
    monkeypatch.setattr(scoring, "_harness_flags", lambda: "--cache_level")
    monkeypatch.setattr(scoring, "_swebench_python", lambda: "/missing/python")
    monkeypatch.setattr(scoring, "_cleanup_eval_containers", lambda *_: None)
    monkeypatch.setattr(
        "importlib.metadata.version",
        lambda _: (_ for _ in ()).throw(RuntimeError("not installed")),
    )

    result = scoring.score_predictions_batch(
        [({"instance_id": "sample"}, "patch", "model")]
    )

    assert "swebench not runnable" in result[("sample", "model")]["error"]

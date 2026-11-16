from types import SimpleNamespace

from snodo.infrastructure.usage_tracker import UsageTracker


def test_failed_completion_usage_and_answering_model_are_recorded_once(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("SNODO_PROJECT_ROOT", str(tmp_path))
    task_id = "task_validator_fallback_usage"
    tracker = UsageTracker()

    partial_response = SimpleNamespace(
        usage=SimpleNamespace(prompt_tokens=17, completion_tokens=5),
        model="provider-a-served",
    )
    error = RuntimeError("provider interrupted after partial work")
    error.response = partial_response
    tracker.log_failure_event(
        {
            "model": "validator-a",
            "metadata": {"task_id": task_id, "role": "validator:security"},
        },
        error,
        0,
        1,
    )
    tracker.log_success_event(
        {
            "model": "validator-b",
            "metadata": {"task_id": task_id, "role": "validator:security"},
        },
        SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=23, completion_tokens=7),
            model="provider-b-served",
        ),
        1,
        2,
    )

    import json

    records = json.loads(
        (tmp_path / ".snodo" / "tasks" / task_id / "state.json").read_text()
    )["usage"]
    assert len(records) == 2
    assert [(r["model"], r["outcome"]) for r in records] == [
        ("validator-a", "error"),
        ("validator-b", "success"),
    ]
    assert [(r["input_tokens"], r["output_tokens"]) for r in records] == [
        (17, 5),
        (23, 7),
    ]

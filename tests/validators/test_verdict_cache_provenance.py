"""Accountable cache reuse: identity is evidence, not an opaque key (#951)."""

import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest

from snodo.core.interfaces import Task, ValidatorResult, result_record
from snodo.validators.verdict_cache import SCHEMA_VERSION, VerdictCache, question_key
from tests.validators.test_verdict_cache import (
    _cache,
    _protocol,
    _run,
    _stored_question,
    _validator,
    sentinel_registry as _sentinel_registry_fixture,
)

sentinel_registry = _sentinel_registry_fixture


@pytest.fixture
def judged(tmp_path, sentinel_registry):
    """A deterministic real verdict, without a provider or tool-loop dependency."""
    sentinel_registry._result = ValidatorResult(
        validator_id="sentinel", severity="warn", justification="inspect this"
    )
    validator = _validator(vid="sentinel", validator_type="sentinel_test")
    protocol = _protocol([validator])
    cache = _cache(tmp_path)

    def run(**kwargs):
        return _run(protocol, [validator], cache, None, **kwargs)[0][0]

    return cache, run, sentinel_registry


def test_provenance_survives_disk_reload_and_canonical_record(judged):
    cache, run, judge = judged
    first = run(task=Task(id="ticket-951", spec="private task specification"))
    assert not first.reused
    payload = json.loads(cache.path.read_text(encoding="utf-8"))
    assert payload["schema"] == SCHEMA_VERSION
    key, entry = next(iter(payload["entries"].items()))
    question = entry["question"]
    assert question["subject"]["id"] == "ticket-951"
    assert question["subject"]["kind"] == "spec"
    assert len(question["subject"]["digest"]) == 64
    assert question["model"] == "test-model"
    assert question["phase"] == "pre_execute"
    assert question["mode_id"] == "producer"
    assert question["protocol_id"] == "p_cache"
    assert question_key(question) == key
    assert "private task specification" not in json.dumps(payload)

    reloaded = VerdictCache(cache.path, project_root=cache.project_root)
    cache._entries = reloaded._entries
    second = run(task=Task(id="ticket-951", spec="private task specification"))
    assert second.reused
    provenance = result_record(second)["cache_provenance"]
    assert provenance == {
        "key": key, "question": question, "stored_at": entry["stored_at"]
    }
    assert second.model_dump()["cache_provenance"] == provenance
    assert judge.calls == 1
    # Neither a display consumer nor a cache inspector can mutate stored evidence.
    provenance["question"]["subject"]["id"] = "mutated"
    inspected = cache.get(key)
    inspected["question"]["subject"]["id"] = "mutated again"
    assert cache.get(key)["question"]["subject"]["id"] == "ticket-951"
    assert second.cache_provenance["question"]["subject"]["id"] == "ticket-951"


@pytest.mark.parametrize("field", ["subject", "model", "criteria_digest", "phase", "mode_id"])
def test_recorded_question_mismatch_is_fresh_even_under_expected_key(judged, field):
    cache, run, judge = judged
    run()
    key = next(iter(cache._entries))
    if field == "subject":
        cache._entries[key]["question"][field]["id"] = "another-task"
    else:
        cache._entries[key]["question"][field] = "different"
    result = run()
    assert not result.reused
    assert result.severity == "warn"
    assert not result.error
    assert judge.calls == 2


def test_distinct_tasks_with_identical_specs_do_not_share_verdicts(judged):
    cache, run, judge = judged
    run(task=Task(id="first", spec="identical"))
    second = run(task=Task(id="second", spec="identical"))
    assert not second.reused
    assert judge.calls == 2
    assert len(cache) == 2


@pytest.mark.parametrize("damage", [
    {"severity": "not-a-verdict"}, {"validator_id": "other"},
    {"stored_at": None}, {"stored_at": float("nan")},
    {"justification": None}, {"question": None},
    {"error": True}, {"skipped": True}, {"cacheable": False},
])
def test_unusable_record_is_a_miss_not_a_validator_error(judged, damage):
    cache, run, judge = judged
    expected = run()
    key = next(iter(cache._entries))
    cache._entries[key].update(damage)
    actual = run()
    assert not actual.reused
    assert result_record(actual) == result_record(expected)
    assert not actual.error
    assert judge.calls == 2


@pytest.mark.parametrize("fault", ["lookup", "store", "subject", "reconstruct"])
def test_cache_faults_leave_fresh_verdict_unchanged(judged, fault):
    cache, run, judge = judged
    expected = run()
    if fault == "lookup":
        target = patch.object(cache, "get", side_effect=RuntimeError("cache fault"))
    elif fault == "store":
        cache.clear()
        target = patch.object(cache, "put", side_effect=RuntimeError("cache fault"))
    else:
        name = "_verdict_subject" if fault == "subject" else "reused_result"
        target = patch("snodo.validators.runner." + name, side_effect=RuntimeError("cache fault"))
    with target:
        actual = run()
    assert result_record(actual) == result_record(expected)
    assert not actual.error
    assert judge.calls == 2


def test_old_shape_is_discarded_even_if_entry_looks_usable(judged):
    cache, run, _ = judged
    run()
    payload = json.loads(cache.path.read_text(encoding="utf-8"))
    payload["schema"] = 1
    cache.path.write_text(json.dumps(payload), encoding="utf-8")
    assert len(VerdictCache(cache.path, project_root=cache.project_root)) == 0


def test_non_cacheable_verdict_is_never_stored_or_reused(judged):
    cache, run, judge = judged
    judge._result = ValidatorResult(
        validator_id="sentinel", severity="pass", justification="fallback", cacheable=False
    )
    assert not run().reused
    assert not run().reused
    assert judge.calls == 2
    assert len(cache) == 0


def test_provenance_survives_recovery_and_severity_caps(judged):
    cache, run, judge = judged
    task = Task(id="recovery", spec="fix", depth=1)
    run(task=task)
    reused = run(task=task)
    assert reused.severity == "pass"
    assert reused.reused
    assert reused.cache_provenance["question"]["subject"]["id"] == task.id
    assert judge.calls == 1


def test_concurrent_storage_keeps_provenance_isolated_and_bounded(tmp_path):
    cache = VerdictCache(tmp_path / "cache.json", max_entries=5)

    def store(index):
        question = _stored_question(subject=str(index))
        key = question_key(question)
        cache.put(key, ValidatorResult(validator_id="v", severity="pass", justification="ok"), question=question)
        record = cache.get(key)
        if record is not None:
            assert record["question"] == question
            record["question"]["subject"]["id"] = "changed"

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(store, range(30)))
    assert len(cache) == 5
    payload = json.loads(cache.path.read_text(encoding="utf-8"))
    for key, record in payload["entries"].items():
        assert key == question_key(record["question"])
        assert record["question"]["subject"]["id"] == "t1"

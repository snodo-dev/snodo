import pytest

from snodo.infrastructure.audit import _LOCAL_EVENT_DATA_KEYS
from snodo.infrastructure.intent_store import IntentError, IntentStore


class Audit:
    def __init__(self):
        self.events = []

    def append_event(self, kind, data):
        self.events.append((kind, data))


def test_record_read_list_and_local_audit(tmp_path):
    audit = Audit()
    store = IntentStore(tmp_path, audit)
    one = store.record("First", issue="#1042")
    two = store.record("Correction", supersedes=one["id"])
    assert store.get(one["id"]) == one
    assert [r["id"] for r in store.list()] == [two["id"], one["id"]]
    assert audit.events[-1] == ("intent_recorded", {"intent_id": two["id"], "issue": None, "supersedes": one["id"]})
    assert tuple(audit.events[-1][1]) == _LOCAL_EVENT_DATA_KEYS["intent_recorded"]


def test_validation(tmp_path):
    store = IntentStore(tmp_path, Audit())
    for text in ("", "  "):
        with pytest.raises(IntentError):
            store.record(text)
    with pytest.raises(IntentError, match="exceeds"):
        store.record("x" * 10_001)
    with pytest.raises(IntentError, match="not found"):
        store.record("x", supersedes="int_000000")
    with pytest.raises(IntentError, match="Malformed"):
        store.get("../bad")


def test_atomic_file_is_complete(tmp_path):
    store = IntentStore(tmp_path, Audit())
    record = store.record("durable")
    assert (store.directory / record["id"] / "intent.json").read_text().endswith("\n")
    assert list((store.directory / record["id"]).iterdir()) == [store.directory / record["id"] / "intent.json"]

"""Plan lookup from immutable intent records."""
import json

import yaml

from snodo.infrastructure.intent_store import IntentStore
from snodo.infrastructure.intent_trace import plans_for_intent


def _plan(root, name, ref, statuses=None):
    directory = root / ".snodo" / "plans" / name
    directory.mkdir(parents=True)
    (directory / "plan.yml").write_text(yaml.safe_dump({
        "name": name, "intent": "example", "intent_ref": ref,
        "waves": [{"id": "w1", "tasks": ["task"]}],
    }))
    if statuses is not None:
        (directory / "status.json").write_text(json.dumps({"tasks": statuses}))


def test_plans_for_intent_empty_and_one(tmp_path):
    intent = IntentStore(tmp_path).record("Intent")
    assert plans_for_intent(tmp_path, intent["id"]) == []
    _plan(tmp_path, "one", intent["id"], {"task": {"status": "completed"}})
    assert plans_for_intent(tmp_path, intent["id"]) == [
        {"name": "one", "status_counts": {"completed": 1}},
    ]


def test_plans_for_intent_multiple_skips_malformed(tmp_path):
    intent = IntentStore(tmp_path).record("Intent")
    _plan(tmp_path, "one", intent["id"], {"a": {"status": "pending"}})
    _plan(tmp_path, "two", intent["id"], {"b": "blocked"})
    broken = tmp_path / ".snodo" / "plans" / "broken"
    broken.mkdir()
    (broken / "plan.yml").write_text("[malformed: yaml")
    assert plans_for_intent(tmp_path, intent["id"]) == [
        {"name": "one", "status_counts": {"pending": 1}},
        {"name": "two", "status_counts": {"blocked": 1}},
    ]

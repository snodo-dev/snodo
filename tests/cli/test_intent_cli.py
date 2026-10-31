"""CLI coverage for local intent records."""
import json

import yaml
from typer.testing import CliRunner

from snodo.cli.commands.intent_cmd import app

runner = CliRunner()


def test_record_show_list_and_json(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".snodo").mkdir()
    recorded = runner.invoke(app, ["record", "Ship safely", "--issue", "#12", "--json"])
    assert recorded.exit_code == 0, recorded.output
    item = json.loads(recorded.output)["intent"]
    assert item["text"] == "Ship safely"
    shown = runner.invoke(app, ["show", item["id"], "--json"])
    assert json.loads(shown.output)["intent"] == {**item, "plans": []}
    listed = runner.invoke(app, ["list", "--json"])
    assert json.loads(listed.output)["intents"] == [item]
    assert runner.invoke(app, ["list"]).exit_code == 0


def test_store_errors_are_reported(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".snodo").mkdir()
    result = runner.invoke(app, ["show", "int_abcdef"])
    assert result.exit_code != 0
    assert "Intent not found" in result.output
    bad = runner.invoke(app, ["record", "   ", "--json"])
    assert bad.exit_code != 0
    assert json.loads(bad.output)["ok"] is False


def test_supersedes_requires_existing_intent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".snodo").mkdir()
    older = runner.invoke(app, ["record", "Old intent", "--json"])
    assert older.exit_code == 0, older.output
    older_item = json.loads(older.output)["intent"]
    newer = runner.invoke(app, ["record", "Updated intent", "--supersedes", older_item["id"], "--json"])
    assert newer.exit_code == 0, newer.output
    newer_item = json.loads(newer.output)["intent"]
    assert newer_item["supersedes"] == older_item["id"]

    unknown = runner.invoke(app, ["record", "Invalid reference", "--supersedes", "int_abcdef"])
    assert unknown.exit_code != 0
    assert "Intent" in unknown.output


def test_show_lists_plans_that_name_the_intent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".snodo").mkdir()
    recorded = runner.invoke(app, ["record", "Trace me", "--json"])
    item = json.loads(recorded.output)["intent"]
    plan_dir = tmp_path / ".snodo" / "plans" / "traced"
    plan_dir.mkdir(parents=True)
    (plan_dir / "plan.yml").write_text(yaml.safe_dump({
        "name": "traced", "intent": "example", "intent_ref": item["id"],
        "waves": [{"id": "w1", "tasks": ["task"]}],
    }))
    (plan_dir / "status.json").write_text(json.dumps({"tasks": {"task": {"status": "completed"}}}))

    shown = runner.invoke(app, ["show", item["id"], "--json"])
    assert shown.exit_code == 0, shown.output
    assert json.loads(shown.output)["intent"]["plans"] == [
        {"name": "traced", "status_counts": {"completed": 1}},
    ]
    text = runner.invoke(app, ["show", item["id"]])
    assert "Plan: traced (completed: 1)" in text.output

    other = json.loads(runner.invoke(app, ["record", "No plans", "--json"]).output)["intent"]
    assert json.loads(runner.invoke(app, ["show", other["id"], "--json"]).output)["intent"]["plans"] == []

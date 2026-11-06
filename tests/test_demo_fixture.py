import json
import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SELECTION_FILE = ROOT / "experiments/tasks/s47.selection.jsonl"
DEMO_DIR = ROOT / "demo/scikit-learn-26194"
EXPECTED_KEYS = {
    "instance_id",
    "repo",
    "repo_url",
    "base_commit",
    "fail_to_pass",
    "pass_to_pass",
}


def _selection_row():
    if not SELECTION_FILE.is_file():
        pytest.skip(f"Selection file is absent: {SELECTION_FILE}")
    with SELECTION_FILE.open(encoding="utf-8", newline="") as stream:
        for line_number, line in enumerate(stream, start=1):
            if line_number == 67:
                return json.loads(line)
    pytest.fail(f"Selection file has no line 67: {SELECTION_FILE}")


def test_demo_fixture_matches_selection_row_without_solution():
    row = _selection_row()
    task_path = DEMO_DIR / "task.md"
    with task_path.open(encoding="utf-8", newline="") as stream:
        assert stream.read() == row["problem_statement"]

    with (DEMO_DIR / "instance.json").open(encoding="utf-8") as stream:
        instance = json.load(stream)
    assert set(instance) == EXPECTED_KEYS
    assert instance["instance_id"] == row["instance_id"]
    assert instance["repo"] == "scikit-learn/scikit-learn"
    assert instance["repo_url"] == "https://github.com/scikit-learn/scikit-learn"
    assert re.fullmatch(r"[0-9a-fA-F]{40}", instance["base_commit"])
    assert instance["base_commit"] == row["base_commit"]
    assert instance["fail_to_pass"] == json.loads(row["FAIL_TO_PASS"])
    assert instance["pass_to_pass"] == json.loads(row["PASS_TO_PASS"])

    solution_text = [row[key] for key in ("patch", "test_patch") if row.get(key)]
    for path in DEMO_DIR.rglob("*"):
        if path.is_file():
            with path.open(encoding="utf-8", newline="") as stream:
                contents = stream.read()
            for solution in solution_text:
                assert solution not in contents, f"Solution text found in {path}"

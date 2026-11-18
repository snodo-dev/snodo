"""Tests for untrusted ADR proposal parsing and evidence checks."""

import json

from snodo.survey.adr_proposals import parse_adr_proposals


def _proposal(**overrides):
    proposal = {
        "title": "Use a durable queue",
        "context": "Work must survive process restarts.",
        "decision": "Persist queued work before acknowledging it.",
        "consequences": "Recovery can replay persisted work.",
        "evidence": [
            {"path": "one.txt", "line_range": [1, 1]},
            {"path": "two.txt"},
            {"path": "three.txt"},
        ],
    }
    proposal.update(overrides)
    return proposal


def _repo(tmp_path):
    for name in ("one.txt", "two.txt", "three.txt"):
        (tmp_path / name).write_text("one line\nsecond line\n")
    return tmp_path


def test_keeps_grounded_proposal(tmp_path):
    root = _repo(tmp_path)
    accepted, dropped = parse_adr_proposals(json.dumps([_proposal()]), root)
    assert len(accepted) == 1
    assert accepted[0].title == "Use a durable queue"
    assert accepted[0].evidence[0].line_range == (1, 1)
    assert dropped == []


def test_drops_proposals_without_three_distinct_evidence_files(tmp_path):
    root = _repo(tmp_path)
    item = _proposal(evidence=[{"path": "one.txt"}, {"path": "one.txt"}])
    accepted, dropped = parse_adr_proposals(json.dumps([item]), root)
    assert not accepted
    assert "three different files" in dropped[0]


def test_drops_missing_evidence_file(tmp_path):
    root = _repo(tmp_path)
    item = _proposal(evidence=[{"path": "one.txt"}, {"path": "two.txt"}, {"path": "absent.txt"}])
    accepted, dropped = parse_adr_proposals(json.dumps([item]), root)
    assert not accepted
    assert "does not exist" in dropped[0]


def test_drops_out_of_range_line_range(tmp_path):
    root = _repo(tmp_path)
    item = _proposal(evidence=[{"path": "one.txt", "line_range": [1, 3]}, {"path": "two.txt"}, {"path": "three.txt"}])
    accepted, dropped = parse_adr_proposals(json.dumps([item]), root)
    assert not accepted
    assert "outside the file" in dropped[0]


def test_drops_empty_field(tmp_path):
    accepted, dropped = parse_adr_proposals(json.dumps([_proposal(context=" ")]), _repo(tmp_path))
    assert not accepted
    assert "non-empty" in dropped[0]


def test_drops_duplicate_title_ignoring_number_prefix(tmp_path):
    accepted, dropped = parse_adr_proposals(
        json.dumps([_proposal(title="ADR 007 — Use a durable queue")]),
        _repo(tmp_path),
        existing_titles=["002: Use a durable queue"],
    )
    assert not accepted
    assert "duplicates" in dropped[0]


def test_reads_fenced_json_and_json_in_prose(tmp_path):
    root = _repo(tmp_path)
    data = json.dumps([_proposal()])
    fenced, _ = parse_adr_proposals(f"```json\n{data}\n```", root)
    prose, _ = parse_adr_proposals(f"Here are proposals: {data} Hope that helps.", root)
    assert len(fenced) == len(prose) == 1


def test_malformed_json_returns_reason_not_exception(tmp_path):
    accepted, dropped = parse_adr_proposals("not json at all", _repo(tmp_path))
    assert accepted == []
    assert len(dropped) == 1
    assert "Malformed" in dropped[0]


def test_caps_kept_proposals_and_preserves_order(tmp_path):
    root = _repo(tmp_path)
    first = _proposal()
    second = _proposal(title="Choose the second option")
    accepted, _ = parse_adr_proposals(json.dumps([first, second]), root, limit=1)
    assert [proposal.title for proposal in accepted] == [first["title"]]

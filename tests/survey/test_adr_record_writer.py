"""Tests for writing accepted ADR proposals as decision records."""

from pathlib import Path

from snodo.survey.adr_proposals import ADRProposal, EvidenceReference
from snodo.survey.adr_record_writer import write_adr_record


def _proposal(title="Use a durable queue"):
    return ADRProposal(
        title=title,
        context="Work must survive process restarts.",
        decision="Persist queued work before acknowledging it.",
        consequences="Recovery can replay persisted work.",
        evidence=(EvidenceReference("src/queue.py", (2, 4)), EvidenceReference("tests/test_queue.py")),
    )


def test_writes_next_number_and_expected_sections(tmp_path):
    (tmp_path / "003-existing.md").write_text("existing")
    (tmp_path / "001-old.md").write_text("old")
    path = write_adr_record(_proposal(), tmp_path)
    content = path.read_text()
    assert path.name == "004-use-a-durable-queue.md"
    assert "# ADR 004 — Use a durable queue" in content
    assert "## Status\n\nProposed" in content
    for section in ("Context", "Decision", "Consequences", "Alternatives considered", "Evidence"):
        assert f"## {section}" in content
    assert "`src/queue.py` (lines 2–4)" in content


def test_empty_folder_starts_at_001(tmp_path):
    assert write_adr_record(_proposal(), tmp_path).name == "001-use-a-durable-queue.md"


def test_does_not_overwrite_taken_name_and_uses_next_number(tmp_path):
    occupied = tmp_path / "001-use-a-durable-queue.md"
    occupied.write_text("keep me")
    result = write_adr_record(_proposal(), tmp_path)
    assert result.name == "002-use-a-durable-queue.md"
    assert occupied.read_text() == "keep me"


def test_punctuation_produces_safe_filename(tmp_path):
    result = write_adr_record(_proposal("What about C++ / APIs?"), tmp_path)
    assert result.name == "001-what-about-c-apis.md"
    assert Path(result).parent == tmp_path

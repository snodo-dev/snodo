"""Tests for WorkspaceMCP.summarize_directory.

The documents this tool exists for carry no YAML front matter: they open with a
level-one heading and plain "Key: value" lines before the first subheading. The
fixtures below use that convention deliberately — a front-matter parser passes
against a `---` fixture that no real corpus uses, and returns nothing on the
documents that matter.
"""

import pytest
from snodo.tools.workspace import (
    PathValidationError,
    WorkspaceMCP,
    _MAX_LEADING_FIELDS,
    _MAX_SUMMARY_FILES,
)


@pytest.fixture
def workspace(tmp_path):
    return WorkspaceMCP(str(tmp_path))


def test_indexes_real_document_shape(workspace, tmp_path):
    """Heading + leading key-value lines, no front matter."""
    decisions = tmp_path / "docs" / "decisions"
    decisions.mkdir(parents=True)
    (decisions / "0068-oauth.md").write_text(
        "# 0068 Use OAuth2\n"
        "\n"
        "Status: Accepted\n"
        "Supersedes: none\n"
        "Date: 2024-01-01\n"
        "\n"
        "## Context\n"
        "The prose body is not part of the record.\n"
        "Status: this line is past the first subheading and must be ignored.\n"
    )

    out = workspace.summarize_directory("docs/decisions")

    assert "docs/decisions/0068-oauth.md" in out
    assert "# 0068 Use OAuth2" in out
    assert "Status: Accepted" in out
    assert "Supersedes: none" in out
    assert "Date: 2024-01-01" in out
    # Body content past the subheading is not read into the record.
    assert "prose body" not in out


def test_document_with_heading_only(workspace, tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "bare.md").write_text("# Just A Heading\n")

    out = workspace.summarize_directory("docs")

    assert "docs/bare.md" in out
    assert "# Just A Heading" in out


def test_document_with_neither(workspace, tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "plain.md").write_text("no heading, no key-value lines\n")

    out = workspace.summarize_directory("docs")

    assert "docs/plain.md" in out


def test_nonexistent_directory_is_an_answer_not_an_error(workspace):
    out = workspace.summarize_directory("docs/nope")

    assert "No documents found" in out
    assert "does not exist" in out


def test_path_outside_workspace_is_refused(workspace):
    with pytest.raises(PathValidationError):
        workspace.summarize_directory("../outside")


def test_field_cap_is_stated(workspace, tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "many.md").write_text(
        "# Many Fields\n"
        + "\n".join(f"Key{i}: value{i}" for i in range(_MAX_LEADING_FIELDS + 3))
        + "\n## Sub\n"
    )

    out = workspace.summarize_directory("docs")

    assert f"Key{_MAX_LEADING_FIELDS - 1}: value{_MAX_LEADING_FIELDS - 1}" in out
    assert f"Key{_MAX_LEADING_FIELDS + 2}:" not in out
    assert "..." in out


def test_large_corpus_states_truncation(workspace, tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    for i in range(_MAX_SUMMARY_FILES + 5):
        (docs / f"{i:04d}.md").write_text(f"# Doc {i}\nStatus: Accepted\n")

    out = workspace.summarize_directory("docs")

    assert "[truncated:" in out
    assert f"of {_MAX_SUMMARY_FILES + 5} documents" in out
    # The bound held: no more than the file cap was emitted.
    assert out.count("Status: Accepted") <= _MAX_SUMMARY_FILES


def test_hidden_and_binary_files_are_skipped(workspace, tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / ".hidden.md").write_text("# Hidden\n")
    (docs / "data.bin").write_bytes(b"\xff\xfe\x00\x01binary")
    (docs / "real.md").write_text("# Real\nStatus: Accepted\n")

    out = workspace.summarize_directory("docs")

    assert "real.md" in out
    assert "hidden" not in out
    assert "data.bin" not in out


def test_no_documents_reported_for_empty_directory(workspace, tmp_path):
    (tmp_path / "docs").mkdir()

    out = workspace.summarize_directory("docs")

    assert "No documents found" in out

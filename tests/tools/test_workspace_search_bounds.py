from snodo.tools.workspace import WorkspaceMCP


def test_search_string_truncates_huge_matching_line(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "huge.txt").write_text("prefix needle " + "x" * 5_000_000)

    result = WorkspaceMCP(str(project)).search_string("needle")

    assert result.startswith("huge.txt:1: ")
    assert "... [line truncated]" in result
    assert len(result) < 2_000


def test_search_string_keeps_ordinary_matching_lines_unchanged(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "ordinary.txt").write_text("before\nordinary needle line\nafter\n")

    result = WorkspaceMCP(str(project)).search_string("needle")

    assert result == "ordinary.txt:2: ordinary needle line"

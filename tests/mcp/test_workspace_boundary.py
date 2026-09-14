"""Boundary tests for WorkspaceMCP read tools (Fixes #273).

Two problems wear the same clothes:

* Containment — a path that escapes the workspace root, including through a
  symlink the tree walk meets, must be refused like any other invalid path.
* Judgement — version-control internals and derived build output are inside
  the workspace and readable by design, but they are not the work.  A read of
  one is refused with a message naming the reason rather than returning content
  or silence.

Derived output is distinguished by what the project declares about itself: its
git ignore rules.  A path git ignores and does not track is derived output; a
tracked file stays readable even inside an ignored-looking directory.
"""

import subprocess

import pytest
from snodo.tools.workspace import PathValidationError, WorkspaceMCP


def _init_repo(root) -> None:
    """Create a git repo with tracked source, ignored output, and a VCS dir."""
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)
    (root / ".gitignore").write_text("dist/\nbuild/\n*.log\n")
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("print('real source')\n")
    # A tracked file that lives in a directory whose name looks generated: the
    # project's own decision must defeat the guess, so it stays readable.
    (root / "dist").mkdir()
    (root / "dist" / "keep.py").write_text("tracked_source = True\n")
    subprocess.run(["git", "add", ".gitignore", "src/app.py"], cwd=root, check=True)
    subprocess.run(["git", "add", "-f", "dist/keep.py"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=root, check=True)
    # Untracked, ignored derived output, and a log.
    (root / "dist" / "bundle.css").write_text("body{}\n")
    (root / "build").mkdir()
    (root / "build" / "out.js").write_text("compiled\n")
    (root / "debug.log").write_text("ignored\n")


@pytest.fixture
def repo(tmp_path):
    _init_repo(tmp_path)
    return tmp_path


@pytest.fixture
def workspace(repo):
    return WorkspaceMCP(str(repo))


#: Every read tool that takes a path, with (a) an escaping path and (b) whether
#: the tool raises versus returns a sentinel.  ``file_exists`` is the one tool
#: whose contract answers a bad path with False rather than an exception.
_ESCAPING_READS = [
    ("read_file", lambda ws, p: ws.read_file(p)),
    ("read_file_lines", lambda ws, p: ws.read_file_lines(p, 1, 1)),
    ("list_files", lambda ws, p: ws.list_files(p)),
    ("summarize_directory", lambda ws, p: ws.summarize_directory(p)),
    ("search_string", lambda ws, p: ws.search_string("x", p)),
    ("search_symbol", lambda ws, p: ws.search_symbol("x", p)),
    ("get_absolute_path", lambda ws, p: ws.get_absolute_path(p)),
]


@pytest.mark.parametrize("name,call", _ESCAPING_READS, ids=[n for n, _ in _ESCAPING_READS])
def test_every_read_tool_refuses_a_path_above_the_workspace(workspace, name, call):
    """A path escaping the workspace root is refused by every read tool."""
    with pytest.raises(PathValidationError, match="escapes project root"):
        call(workspace, "../outside.txt")


@pytest.mark.parametrize("name,call", _ESCAPING_READS, ids=[n for n, _ in _ESCAPING_READS])
def test_every_read_tool_refuses_an_absolute_path_above_the_workspace(workspace, name, call):
    with pytest.raises(PathValidationError, match="escapes project root"):
        call(workspace, "/etc/passwd")


def test_file_exists_does_not_leak_above_the_workspace(workspace):
    """``file_exists`` answers a traversal with False, never with the truth."""
    assert workspace.file_exists("../outside.txt") is False
    assert workspace.file_exists("/etc/passwd") is False


# --- Containment through a symlink ------------------------------------------


def test_symlinked_file_outside_the_workspace_is_not_read(workspace, repo, tmp_path):
    """A symlink resolving outside the root is refused by a direct read."""
    target = tmp_path.parent / "outside-secret.txt"
    target.write_text("secret\n")
    link = repo / "src" / "escape.txt"
    link.symlink_to(target)

    with pytest.raises(PathValidationError, match="escapes project root"):
        workspace.read_file("src/escape.txt")


def test_search_does_not_follow_a_symlink_outside_the_workspace(workspace, repo, tmp_path):
    """The tree-walking search tools do not open a symlink that escapes the root."""
    target = tmp_path.parent / "outside-secret.txt"
    target.write_text("canary_outside_value\n")
    (repo / "src" / "escape.txt").symlink_to(target)

    result = workspace.search_string("canary_outside_value")

    # The query is echoed even with no matches; the secret body must not be.
    assert "escape.txt:1" not in result
    assert "No matches found" in result


def test_summarize_does_not_read_a_symlink_outside_the_workspace(workspace, repo, tmp_path):
    target = tmp_path.parent / "outside-secret.txt"
    target.write_text("# Secret\n")
    (repo / "src" / "escape.md").symlink_to(target)

    result = workspace.summarize_directory("src")

    assert "escape.md" not in result


def test_listing_does_not_offer_a_symlink_outside_the_workspace(workspace, repo, tmp_path):
    target = tmp_path.parent / "outside-secret.txt"
    target.write_text("secret\n")
    (repo / "src" / "escape.txt").symlink_to(target)

    assert "escape.txt" not in workspace.list_files("src")


# --- Version-control internals ----------------------------------------------


@pytest.mark.parametrize("path", [".git", ".git/config", ".git/logs/HEAD", ".GIT/logs/HEAD"])
def test_vcs_internals_are_refused_naming_the_reason(workspace, path):
    """A read of VCS bookkeeping is refused, and the refusal names the reason."""
    with pytest.raises(PathValidationError, match="version-control bookkeeping"):
        workspace.read_file(path)


def test_vcs_internals_are_refused_through_git_show(workspace, repo):
    """GitMCP.show applies the same boundary."""
    from snodo.tools.git import GitMCP

    git = GitMCP(str(repo))
    with pytest.raises(PathValidationError, match="version-control bookkeeping"):
        git.show("HEAD", ".git/config")


# --- Derived build output ---------------------------------------------------


@pytest.mark.parametrize("path", ["dist/bundle.css", "build/out.js", "debug.log"])
def test_derived_output_is_refused_naming_the_reason(workspace, path):
    """A read of ignored, untracked derived output names why it is not shown."""
    with pytest.raises(PathValidationError, match="derived build output"):
        workspace.read_file(path)


def test_derived_output_names_the_declaration_source(workspace):
    with pytest.raises(PathValidationError, match=r"\.gitignore"):
        workspace.read_file("build/out.js")


def test_listing_omits_derived_output(workspace):
    """A listing does not offer derived output as though it were the work."""
    names = workspace.list_files(".")

    assert "build" not in names
    assert "debug.log" not in names
    assert "src" in names


def test_search_does_not_read_derived_output(workspace):
    """A text search never opens a derived file it would then have to refuse."""
    result = workspace.search_string("compiled")

    assert "out.js" not in result


def test_tracked_source_in_an_ignored_looking_directory_stays_readable(workspace):
    """A project that tracks source under a generated-looking name keeps it.

    This is the case a fixed directory-name list would have hidden.
    """
    assert workspace.read_file("dist/keep.py") == "tracked_source = True\n"
    assert "keep.py" in workspace.list_files("dist")


def test_snodo_state_stays_readable(workspace, repo):
    """`.snodo/` is gitignored in every governed project but stays readable (ADR 026)."""
    snodo = repo / ".snodo"
    snodo.mkdir()
    (snodo / "protocol.yml").write_text("name: Test\n")

    assert workspace.read_file(".snodo/protocol.yml") == "name: Test\n"
    assert "protocol.yml" in workspace.list_files(".snodo")


def test_a_workspace_without_git_declares_nothing(tmp_path):
    """A non-repository declares nothing, so nothing is refused as derived."""
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "dist").mkdir()
    (plain / "dist" / "card.css").write_text("body{}\n")
    ws = WorkspaceMCP(str(plain))

    assert ws.read_file("dist/card.css") == "body{}\n"

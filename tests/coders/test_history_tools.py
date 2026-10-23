from git import Repo

from snodo.coders.history_tools import execute
from snodo.coders.litellm import LiteLLMAdapter
from snodo.coders.tool_result_text import MAX_TOOL_RESULT_CHARS
from snodo.tools.workspace import WorkspaceMCP


def _history_workspace(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    repo = Repo.init(root)
    path = root / "example.txt"
    path.write_text("historical content\n")
    repo.index.add(["example.txt"])
    old = repo.index.commit("add example")
    path.write_text("current content\n")
    repo.index.add(["example.txt"])
    repo.index.commit("update example")
    return WorkspaceMCP(str(root)), old.hexsha


def test_coder_can_read_file_history(tmp_path):
    workspace, old_ref = _history_workspace(tmp_path)

    log = execute("git_log", {"path": "example.txt"}, workspace)
    historical = execute("git_show", {"ref": old_ref, "path": "example.txt"}, workspace)

    assert "add example" in log
    assert "historical content" in historical
    assert "current content" not in historical


def test_history_results_use_shared_bound(tmp_path):
    workspace, _ = _history_workspace(tmp_path)
    root = workspace.project_root
    repo = Repo(str(root))
    (root / "large.txt").write_text("x" * (MAX_TOOL_RESULT_CHARS + 100))
    repo.index.add(["large.txt"])
    repo.index.commit("large change")
    result = execute("read_diff_between_refs", {"ref1": "HEAD~1", "ref2": "HEAD"}, workspace)
    assert len(result) <= MAX_TOOL_RESULT_CHARS
    assert "TRUNCATED" in result


def test_history_tools_refuse_protected_state_and_unresolvable_ref(tmp_path):
    workspace, _ = _history_workspace(tmp_path)
    assert ".snodo" in execute("git_show", {"ref": "HEAD", "path": ".snodo/secret"}, workspace)
    refusal = execute("git_show", {"ref": "no-such-revision", "path": "example.txt"}, workspace)
    assert "no-such-revision" in refusal
    assert "Could not read" in refusal


def test_coder_history_surface_is_read_only():
    names = {item["function"]["name"] for item in LiteLLMAdapter._build_tool_definitions()}
    assert {"git_log", "git_show", "read_diff_between_refs"} <= names
    assert not {"write_file", "shell", "git_commit"} & names

"""Credential and scoped-write boundaries for .snodo workspace state."""

import pytest

from snodo.tools.workspace import PathValidationError, WorkspaceMCP


def test_snodo_context_is_readable_except_tunnel_credentials(tmp_path):
    state = tmp_path / ".snodo"
    state.mkdir()
    (state / "protocol.yml").write_text("protocol context")
    (state / "plans").mkdir()
    (state / "plans" / "plan.md").write_text("plan context")
    (state / "tunnel.json").write_text('{"tunnel_token":"secret"}')
    workspace = WorkspaceMCP(str(tmp_path))

    assert workspace.read_file(".snodo/protocol.yml") == "protocol context"
    assert workspace.read_file(".snodo/plans/plan.md") == "plan context"
    for operation in (
        lambda: workspace.read_file(".snodo/tunnel.json"),
        lambda: workspace.read_file_lines(".snodo/tunnel.json", 1, 1),
        lambda: workspace.write_file_with_prefixes(
            ".snodo/tunnel.json", "overwrite", [".snodo/"]
        ),
    ):
        with pytest.raises(PathValidationError, match="[Cc]redential"):
            operation()


def test_snodo_writes_require_a_prefix_inside_snodo_and_reject_symlink_escape(tmp_path):
    state = tmp_path / ".snodo"
    state.mkdir()
    (tmp_path / "outside").mkdir()
    (tmp_path / "link").symlink_to(state, target_is_directory=True)
    workspace = WorkspaceMCP(str(tmp_path))

    workspace.write_file_with_prefixes(".snodo/spec.md", "spec", [".snodo/"])
    assert (state / "spec.md").read_text() == "spec"
    for path, prefixes in (
        (".snodo/blocked.md", ["."]),
        ("link/escaped.md", ["link"]),
    ):
        with pytest.raises(PathValidationError):
            workspace.write_file_with_prefixes(path, "blocked", prefixes)

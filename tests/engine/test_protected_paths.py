"""Tests for protocol-declared protected-path diff detection."""

import subprocess
from pathlib import Path

import pytest
from snodo.compiler.models import Mode, Protocol, Validator
from snodo.core.interfaces import Task
from snodo.engine.nodes.validation import ValidationNodeMixin
from snodo.engine.state import LoopState
from snodo.tools.git import GitMCP


class _Git:
    """Stand-in for GitMCP.

    ``project_root`` names no repository on purpose: the base branch cannot be
    resolved, so the guard exercises its ``base_ref`` fallback. The rebase
    semantics themselves are covered against real git below (#908) — a double
    that returns a fabricated ancestor only models the implementation, not the
    history a rebase actually produces.
    """

    project_root = "no-such-repo"

    def __init__(self, changed, ancestor=None, raises=False):
        self.changed = changed
        self.ancestor = ancestor
        self.raises = raises
        self.expected = ("base", "head")
        self.unusable: set[str] = set()
        self.repo = self

    def commit(self, ref):
        if ref in self.unusable:
            raise RuntimeError(f"cannot resolve {ref}")
        return type("Commit", (), {"hexsha": ref})()

    def merge_base(self, base, head):
        if self.raises:
            raise RuntimeError("ancestry unavailable")
        return [self.commit(self.ancestor)] if self.ancestor else []

    def get_head_sha(self):
        return "head"

    def changed_paths_between_refs(self, base, head):
        assert (base, head) == self.expected
        return self.changed


class _Builder(ValidationNodeMixin):
    def __init__(self, protected, changed, ancestor=None, raises=False, git=None):
        self.protocol = Protocol(
            protocol_id="p",
            name="p",
            modes=[Mode(mode_id="m", name="m")],
            validators=[Validator(validator_id="v", validator_type="quality")],
            initial_mode="m",
            protected_paths=protected,
        )
        self.git_mcp = _Git(changed, ancestor, raises) if git is None else git
        self.audit_events = []

    def _audit(self, event, payload):
        self.audit_events.append((event, payload))


def _state():
    return LoopState(
        task=Task(id="t", spec="change code"),
        current_mode="m",
        base_ref="base",
    )


def test_changed_declared_path_is_a_named_blocker():
    result = _Builder(
        ["docs/architecture.md"], ["docs/architecture.md"]
    )._protected_path_result(_state())

    assert result.severity == "blocker"
    assert "docs/architecture.md" in result.justification


def test_changed_descendant_of_declared_path_is_blocked():
    result = _Builder(["docs"], ["docs/architecture.md"])._protected_path_result(
        _state()
    )

    assert result is not None
    assert "docs" in result.justification


def test_unprotected_change_is_unaffected():
    result = _Builder(["docs/architecture.md"], ["src/main.py"])._protected_path_result(
        _state()
    )

    assert result is None


def test_empty_and_absent_declarations_are_unaffected():
    assert _Builder([], ["docs/architecture.md"])._protected_path_result(_state()) is None
    assert _Builder(["docs/architecture.md"], [])._protected_path_result(_state()) is None


def test_rebased_base_history_is_not_attributed_to_task():
    """Fallback path: with no resolvable base branch, the captured base_ref's
    merge-base is used, so a common ancestor still excludes incoming work."""
    builder = _Builder(["docs"], [], ancestor="common")
    builder.git_mcp.expected = ("common", "head")
    assert builder._protected_path_result(_state()) is None


def test_own_change_after_rebase_remains_blocked():
    builder = _Builder(["docs"], ["docs/policy.md"], ancestor="common")
    builder.git_mcp.expected = ("common", "head")
    result = builder._protected_path_result(_state())
    assert result.severity == "blocker"
    assert "docs" in result.justification


def test_missing_merge_base_falls_back_to_captured_base():
    result = _Builder(["docs"], ["docs/policy.md"])._protected_path_result(_state())
    assert result.severity == "blocker"


def test_unusable_base_branch_tip_still_checks_the_captured_ref(monkeypatch):
    """A base branch named by the remote that this checkout cannot resolve is
    not a reason to stop guarding; the captured base_ref still is."""
    import snodo.engine.nodes.validation as validation_nodes

    monkeypatch.setattr(validation_nodes, "resolve_base_branch", lambda root: "origin/main")
    builder = _Builder(["docs"], ["docs/policy.md"])
    builder.git_mcp.unusable = {"origin/main"}

    result = builder._protected_path_result(_state())
    assert result is not None
    assert result.severity == "blocker"
    assert builder.audit_events == []


def test_merge_base_error_is_audited_without_blocker():
    builder = _Builder(["docs"], ["docs/policy.md"], raises=True)
    assert builder._protected_path_result(_state()) is None
    assert builder.audit_events[0][0] == "protected_paths_unchecked"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


def _write(repo: Path, path: str, content: str) -> None:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture()
def repo(tmp_path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-b", "main", ".")
    _git(root, "config", "user.email", "tester@example.com")
    _git(root, "config", "user.name", "Path Tester")
    _write(root, "README.md", "seed\n")
    _commit(root, "seed")
    return root


def _state_at(base_ref: str) -> LoopState:
    return LoopState(
        task=Task(id="t", spec="change code"),
        current_mode="m",
        base_ref=base_ref,
    )


def test_rebase_does_not_blame_incoming_base_history(repo):
    """A protected path changed by *someone else* on the base branch, then
    replayed under the task by a rebase, is not the task's change (#908).

    base_ref is an ancestor of HEAD after a rebase, so merge-base(base_ref,
    HEAD) is base_ref itself and the old diff still named the incoming path.
    """
    base_ref = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-b", "task")
    _write(repo, "src/main.py", "def run():\n    return 1\n")
    _commit(repo, "task work")

    _git(repo, "checkout", "main")
    _write(repo, "docs/policy.md", "merged by another task\n")
    _commit(repo, "other task's protected-path change")

    _git(repo, "checkout", "task")
    _git(repo, "rebase", "main")

    builder = _Builder(["docs"], [], git=GitMCP(str(repo)))
    assert builder._protected_path_result(_state_at(base_ref)) is None


def test_own_protected_change_after_rebase_still_blocked(repo):
    base_ref = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-b", "task")
    _write(repo, "docs/policy.md", "the coder edited it\n")
    _commit(repo, "task touched a protected path")

    _git(repo, "checkout", "main")
    _write(repo, "src/other.py", "x = 1\n")
    _commit(repo, "unrelated base movement")

    _git(repo, "checkout", "task")
    _git(repo, "rebase", "main")

    result = _Builder(["docs"], [], git=GitMCP(str(repo)))._protected_path_result(
        _state_at(base_ref)
    )
    assert result is not None
    assert result.severity == "blocker"
    assert "docs" in result.justification


def test_diverged_base_branch_history_is_not_attributed(repo):
    """Without a rebase the fork point is unchanged; base-branch work that
    only exists on main must never count against the task."""
    base_ref = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-b", "task")
    _write(repo, "src/main.py", "def run():\n    return 1\n")
    _commit(repo, "task work")

    _git(repo, "checkout", "main")
    _write(repo, "docs/policy.md", "merged by another task\n")
    _commit(repo, "other task's protected-path change")

    builder = _Builder(["docs"], [], git=GitMCP(str(repo)))
    assert builder._protected_path_result(_state_at(base_ref)) is None


def test_task_own_change_blocks_without_any_rebase(repo):
    base_ref = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-b", "task")
    _write(repo, "docs/architecture.md", "edited\n")
    _commit(repo, "task touched a protected path")

    result = _Builder(
        ["docs/architecture.md"], [], git=GitMCP(str(repo))
    )._protected_path_result(_state_at(base_ref))
    assert result is not None
    assert "docs/architecture.md" in result.justification

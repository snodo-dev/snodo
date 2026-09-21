"""The completed-task record carries how much changed, against its own base.

FILE: tests/engine/test_change_size_audit.py

The `task_complete` audit event named which files a task touched but said
nothing about how much changed in them (Fixes #377). These assert the
recorded `change_size` matches a known diff against the branch point the
task forked from — not whatever the base branch became while the task ran
— and that a change whose lines are not countable is distinguishable from
"no change".
"""

import subprocess
from unittest.mock import MagicMock

from snodo.compiler.models import Mode, Protocol, Validator
from snodo.engine.loop import GraphBuilder
from snodo.infrastructure.audit import AuditLog
from snodo.tools.git import GitMCP


def _git(root, *args):
    subprocess.run(["git", *args], cwd=str(root), check=True, capture_output=True)


def _make_protocol():
    return Protocol(
        protocol_id="test_protocol",
        name="Test Protocol",
        modes=[Mode(mode_id="producer", name="Producer", tools=["edit"], validators=["v1"])],
        validators=[Validator(validator_id="v1", validator_type="security")],
        initial_mode="producer",
    )


def _complete_state(task_id="task_001"):
    return {
        "task": {"id": task_id, "spec": "Implement feature"},
        "current_mode": "producer",
        "iteration": 1,
        "stage": "move_next",
        "validation_results": [],
        "validation_token": None,
        "artifacts": ["keep.txt"],
        "constraints_passed": True,
        "constraint_violations": [],
        "policy_decision": None,
        "is_complete": True,
        "is_blocked": False,
        "halt_type": None,
        "metadata": {},
        "messages": [],
    }


def _recorded_change_size(tmp_path, root):
    """Complete a task on the checked-out branch and return the audit record."""
    git_mcp = GitMCP(str(root))
    builder = GraphBuilder(
        _make_protocol(), git_mcp=git_mcp, audit_log=MagicMock(spec=AuditLog)
    )
    builder._complete_node(_complete_state())
    _, data = builder._audit_log.append_event.call_args[0]
    return data


def _base_repo(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "T")
    (root / "keep.txt").write_text("one\ntwo\nthree\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")
    return root


class TestChangeSizeRecorded:
    def test_change_size_matches_known_diff_against_own_base(self, tmp_path):
        root = _base_repo(tmp_path)
        branch_point = GitMCP(str(root)).get_head_sha()
        _git(root, "checkout", "-qb", "task/task_001/implement-feature")
        (root / "keep.txt").write_text("one\nTWO\nthree\nfour\nfive\n")
        (root / "added.txt").write_text("a\nb\nc\n")
        _git(root, "add", "-A")
        _git(root, "commit", "-qm", "the task's own work")

        # The base branch moved on AFTER the task forked: work nobody asked
        # this task about must not appear in the task's record.
        _git(root, "checkout", "-q", "main")
        (root / "someone_else.txt").write_text("\n".join(str(i) for i in range(50)) + "\n")
        _git(root, "add", "-A")
        _git(root, "commit", "-qm", "unrelated work merged while the task ran")
        _git(root, "checkout", "-q", "task/task_001/implement-feature")

        data = _recorded_change_size(tmp_path, root)

        size = data["change_size"]
        assert size is not None
        assert size["base_sha"] == branch_point
        assert size["lines_added"] == 6      # TWO, four, five, a, b, c
        assert size["lines_deleted"] == 1    # two
        assert size["files_changed"] == 2
        assert size["capped"] is False
        # The decoy on main never enters the range.
        assert size["files_added"] == 1
        assert size["paths"] == ["added.txt", "keep.txt"]

    def test_capped_change_size_marks_path_list_incomplete(self, tmp_path):
        root = _base_repo(tmp_path)
        _git(root, "checkout", "-qb", "task/task_001/implement-feature")
        for name in ("a.txt", "b.txt", "c.txt"):
            (root / name).write_text("changed\n")
        _git(root, "add", "-A")
        _git(root, "commit", "-qm", "wide task change")

        git = GitMCP(str(root))
        size = git.change_size("main", "HEAD", max_files=2)

        assert size["capped"] is True
        assert size["files_changed"] == 3
        assert size["paths"] == ["a.txt", "b.txt"]
        assert len(size["paths"]) < size["files_changed"]

    def test_uncountable_change_is_not_the_same_zero_as_no_change(self, tmp_path):
        root = _base_repo(tmp_path)
        _git(root, "checkout", "-qb", "task/task_001/implement-feature")
        (root / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x01binary\xff")
        _git(root, "add", "-A")
        _git(root, "commit", "-qm", "binary-only change")

        size = _recorded_change_size(tmp_path, root)["change_size"]

        assert size is not None
        assert size["lines_added"] == 0
        assert size["lines_deleted"] == 0
        # A zero meaning "no lines" would look identical without this.
        assert size["files_binary"] == 1
        assert size["files_changed"] == 1
        assert size["capped"] is False

    def test_unchanged_task_branch_records_real_zeros(self, tmp_path):
        root = _base_repo(tmp_path)
        _git(root, "checkout", "-qb", "task/task_001/implement-feature")

        size = _recorded_change_size(tmp_path, root)["change_size"]

        # A measured nothing is recorded as nothing, with capped False and
        # every count a genuine zero — not nulls.
        assert size["lines_added"] == 0
        assert size["lines_deleted"] == 0
        assert size["files_changed"] == 0
        assert size["files_binary"] == 0
        assert size["capped"] is False

    def test_run_outside_a_task_branch_records_no_measurement(self, tmp_path):
        """A degraded run on the operator's branch must not be told a size.

        ``None`` is the honest absence; a zero on a branch that is not a
        task branch would misattribute the whole working branch's history.
        """
        root = _base_repo(tmp_path)

        data = _recorded_change_size(tmp_path, root)

        assert data["op"] == "task_complete"
        assert data["change_size"] is None

    def test_completion_without_git_records_no_measurement(self):
        """No repository at all: the field is absent-as-unknown, never zero."""
        builder = GraphBuilder(
            _make_protocol(), audit_log=MagicMock(spec=AuditLog)
        )
        builder._complete_node(_complete_state())
        _, data = builder._audit_log.append_event.call_args[0]
        assert data["change_size"] is None

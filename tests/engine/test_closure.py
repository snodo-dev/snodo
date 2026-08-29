"""Tests for the Kleene-closure driver (run_to_closure) failure handling.

FILE: tests/engine/test_closure.py

Guards against the fail-open bug where a graph exception (or a non-dict /
empty result) was reported as "resolved".  ``resolved`` must require positive
completion evidence (``is_complete``), never the mere absence of a failure.
"""

from unittest.mock import MagicMock

import pytest
from snodo.engine.closure import run_to_closure

ROOT_TASK = {"id": "t1", "spec": "build thing", "depth": 0, "parent_task_ref": None}


def _graph(return_value=None, exc=None):
    g = MagicMock()
    if exc is not None:
        g.invoke.side_effect = exc
    else:
        g.invoke.return_value = return_value
    return g


def _success_state():
    return {
        "is_complete": True,
        "is_blocked": False,
        "halt_type": None,
        "spawned_subtasks": [],
        "artifacts": ["a.py"],
    }


def _spawn_state(subtasks):
    return {
        "is_complete": False,
        "is_blocked": False,
        "halt_type": None,
        "spawned_subtasks": subtasks,
    }


def _graph_by_task(states):
    """Graph whose invoke() returns a state keyed by the invoked task id."""
    g = MagicMock()

    def invoke(initial, **kwargs):
        return states[initial["task"]["id"]]

    g.invoke.side_effect = invoke
    return g


class TestGraphFailureIsNotResolved:
    def test_graph_raises_internal_error(self):
        """A graph exception is reported as internal_error, never resolved."""
        audit = MagicMock()
        final, tree = run_to_closure(
            _graph(exc=RuntimeError("coder network failure")),
            ROOT_TASK, "producer", audit_log=audit,
        )
        assert tree.outcome == "internal_error"
        assert tree.outcome != "resolved"
        assert final["is_blocked"] is True
        assert final["halt_type"] == "internal_error"
        assert "coder network failure" in final["error"]
        audit.append_event.assert_any_call(
            "recovery_internal_error", {
                "op": "recovery_internal_error",
                "task_ref": "t1",
                "depth": 0,
                "error": final["error"],
            },
        )

    def test_graph_returns_empty_dict_not_resolved(self):
        """A graph returning {} (no completion signal) is not resolved."""
        audit = MagicMock()
        final, tree = run_to_closure(
            _graph(return_value={}), ROOT_TASK, "producer", audit_log=audit,
        )
        assert tree.outcome == "internal_error"
        assert tree.outcome != "resolved"

    def test_graph_returns_none_not_resolved(self):
        """A graph returning a non-dict is not resolved."""
        audit = MagicMock()
        final, tree = run_to_closure(
            _graph(return_value=None), ROOT_TASK, "producer", audit_log=audit,
        )
        assert tree.outcome == "internal_error"
        assert final["is_blocked"] is True
        assert final["halt_type"] == "internal_error"
        assert "non-dict" in final["error"]

    def test_missing_completion_signal_not_resolved(self):
        """A dict with neither completion nor failure signal is not resolved."""
        audit = MagicMock()
        final, tree = run_to_closure(
            _graph(return_value={"is_blocked": False, "spawned_subtasks": [], "is_complete": False}),
            ROOT_TASK, "producer", audit_log=audit,
        )
        assert tree.outcome == "internal_error"
        assert tree.outcome != "resolved"


class TestHappyPathStillResolves:
    def test_completed_graph_resolves(self):
        """A genuinely completed graph still reports resolved (regression guard)."""
        audit = MagicMock()
        final, tree = run_to_closure(
            _graph(return_value=_success_state()), ROOT_TASK, "producer", audit_log=audit,
        )
        assert tree.outcome == "resolved"
        assert final["is_complete"] is True
        audit.append_event.assert_any_call(
            "recovery_resolved", {
                "op": "recovery_resolved",
                "task_ref": "t1",
                "depth": 0,
                "attempts_used": 1,
            },
        )


class TestLangGraphControlFlow:
    def test_graph_interrupt_propagates(self):
        """LangGraph GraphInterrupt is not swallowed into a resolved state."""
        from langgraph.errors import GraphInterrupt

        with pytest.raises(GraphInterrupt):
            run_to_closure(
                _graph(exc=GraphInterrupt("paused")), ROOT_TASK, "producer",
            )


class TestOverDeepSiblingDoesNotSkipOthers:
    """A per-branch depth violation must not cancel sibling work or the budget."""

    def _run(self, spawned, max_recovery_depth=3, max_total_fix_attempts=10):
        audit = MagicMock()
        # Root spawns the given subtasks; each legal subtask resolves.
        states = {
            "t1": _spawn_state(spawned),
            "legal_a": _success_state(),
            "legal_b": _success_state(),
        }
        final, tree = run_to_closure(
            _graph_by_task(states), ROOT_TASK, "producer",
            audit_log=audit,
            max_recovery_depth=max_recovery_depth,
            max_total_fix_attempts=max_total_fix_attempts,
        )
        return final, tree, audit

    def test_over_deep_sibling_does_not_skip_legal_siblings(self):
        """[over-depth, legal, legal] → both legal siblings execute."""
        spawned = [
            {"id": "over_deep", "depth": 99},
            {"id": "legal_a", "depth": 1},
            {"id": "legal_b", "depth": 1},
        ]
        _, tree, audit = self._run(spawned)

        child_ids = [c.task_id for c in tree.subtasks]
        assert child_ids == ["over_deep", "legal_a", "legal_b"]

        outcomes = {c.task_id: c.outcome for c in tree.subtasks}
        assert outcomes["over_deep"] == "recovery_exhausted"
        assert outcomes["legal_a"] == "resolved"
        assert outcomes["legal_b"] == "resolved"

        # Both legal siblings were actually invoked.
        invoked = [c[0][1]["task_ref"] for c in audit.append_event.call_args_list
                   if c[0][0] == "recovery_resolved"]
        assert "legal_a" in invoked
        assert "legal_b" in invoked

    def test_depth_violation_does_not_zero_global_budget(self):
        """A depth violation consumes no global budget; later siblings still run."""
        spawned = [
            {"id": "over_deep", "depth": 99},
            {"id": "legal_a", "depth": 1},
        ]
        _, tree, _ = self._run(spawned, max_total_fix_attempts=1)

        # legal_a must still execute (budget of 1 was not consumed by the
        # depth violation), so it resolves.
        outcomes = {c.task_id: c.outcome for c in tree.subtasks}
        assert outcomes["over_deep"] == "recovery_exhausted"
        assert outcomes["legal_a"] == "resolved"

    def test_genuine_global_exhaustion_still_stops(self):
        """Genuine global exhaustion still stops processing (regression guard)."""
        spawned = [
            {"id": "legal_a", "depth": 1},
            {"id": "legal_b", "depth": 1},
        ]
        _, tree, _ = self._run(spawned, max_total_fix_attempts=1)

        # Only one unit of budget: legal_a consumes it, legal_b is exhausted.
        outcomes = {c.task_id: c.outcome for c in tree.subtasks}
        assert outcomes["legal_a"] == "resolved"
        assert outcomes["legal_b"] == "recovery_exhausted"

    def test_parent_outcome_when_some_siblings_depth_exhausted(self):
        """Parent is recovery_exhausted when any sibling is depth-exhausted."""
        spawned = [
            {"id": "over_deep", "depth": 99},
            {"id": "legal_a", "depth": 1},
        ]
        _, tree, _ = self._run(spawned)
        assert tree.outcome == "recovery_exhausted"

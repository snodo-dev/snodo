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

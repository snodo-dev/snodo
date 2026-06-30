"""Kleene-closure outer driver for recovery subtask execution (ADR 013 K2/K4).

Invokes the protocol graph for a root task, then recursively consumes
spawned_subtasks until fixpoint (no more subtasks) or one of two bounds
is exhausted:

- max_total_fix_attempts (global cap across the entire closure tree)
- max_recovery_depth    (per-branch depth cap, default 3)
"""

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class ClosureNode:
    """One node in the closure result tree."""
    task_id: str
    depth: int
    outcome: str  # "resolved" | "blocked" | "escalated" | "recovery_exhausted"
    spawned_subtasks: int = 0
    attempts_used: int = 0
    subtasks: List["ClosureNode"] = field(default_factory=list)


def _make_initial_state(task_dict: dict, mode: str) -> dict:
    """Build an initial graph state dict for a (sub)task."""
    return {
        "task": {
            "id": task_dict.get("id", ""),
            "spec": task_dict.get("spec", ""),
            "parent_task_ref": task_dict.get("parent_task_ref"),
            "depth": task_dict.get("depth", 0),
            "flow_type": task_dict.get("flow_type"),
            "wave_id": task_dict.get("wave_id"),
        },
        "current_mode": mode,
        "iteration": 0,
        "stage": "governance",
        "validation_results": [],
        "validation_token": None,
        "artifacts": [],
        "constraints_passed": True,
        "constraint_violations": [],
        "policy_decision": None,
        "pending_disagreement": None,
        "halt_type": None,
        "is_complete": False,
        "is_blocked": False,
        "metadata": {},
        "messages": [],
        "spawned_subtasks": [],
        "needs_recovery": False,
        "needs_spec_authoring": False,
        "spec_authoring_attempts": 0,
    }


def run_to_closure(
    compiled_graph,
    root_task: dict,
    mode: str,
    audit_log=None,
    max_total_fix_attempts: int = 10,
    max_recovery_depth: int = 3,
    session_id: Optional[str] = None,
    thread_config: Optional[dict] = None,
) -> tuple[dict, ClosureNode]:
    """Execute a task to closure, recursing on spawned subtasks.

    Both termination bounds are enforced simultaneously:
    - max_total_fix_attempts (global budget across the tree)
    - max_recovery_depth (per-branch depth cap)

    Args:
        compiled_graph: Compiled LangGraph StateGraph.
        root_task: Task dict (id, spec, depth, parent_task_ref, ...).
        mode: Current protocol mode.
        audit_log: Optional AuditLog for INV4 event logging.
        max_total_fix_attempts: Global cap across the closure tree.
        max_recovery_depth: Per-branch depth cap.
        session_id: Optional session ID to tag audit events.

    Returns:
        (final_state, closure_tree_root)
    """
    remaining = max_total_fix_attempts

    def _audit(event_type: str, data: dict) -> None:
        if audit_log is not None:
            if session_id:
                data["session_id"] = session_id
            audit_log.append_event(event_type, data)

    def _invoke(task_dict: dict) -> dict:
        initial = _make_initial_state(task_dict, mode)
        kwargs = {}
        if thread_config is not None:
            kwargs["config"] = thread_config
        try:
            result = compiled_graph.invoke(initial, **kwargs)
            return result if isinstance(result, dict) else {}
        except Exception:
            return {}

    def _recurse(task_dict: dict, depth: int) -> tuple[dict, ClosureNode]:
        nonlocal remaining

        final = _invoke(task_dict)

        task_id = task_dict.get("id", "?")
        is_blocked = final.get("is_blocked", False)
        halt_type = final.get("halt_type")
        spawned = final.get("spawned_subtasks", [])

        child_nodes: List[ClosureNode] = []
        total_attempts = 1

        if not spawned and not is_blocked:
            outcome = "resolved"
            _audit("recovery_resolved", {
                "op": "recovery_resolved",
                "task_ref": task_id,
                "depth": depth,
                "attempts_used": 1,
            })

        elif spawned and remaining > 0:
            outcome = "resolved"
            for sub in spawned:
                sub_depth = sub.get("depth", depth + 1)

                # Check per-branch depth cap
                if sub_depth > max_recovery_depth:
                    outcome = "recovery_exhausted"
                    _audit("recovery_exhausted", {
                        "op": "recovery_exhausted",
                        "task_ref": sub.get("id", task_id),
                        "depth": sub_depth,
                        "max_depth": max_recovery_depth,
                        "reason": "max_recovery_depth",
                    })
                    child_nodes.append(ClosureNode(
                        task_id=sub.get("id", "?"),
                        depth=sub_depth,
                        outcome="recovery_exhausted",
                    ))
                    remaining = -1
                    break

                remaining -= 1
                if remaining < 0:
                    outcome = "recovery_exhausted"
                    _audit("recovery_exhausted", {
                        "op": "recovery_exhausted",
                        "task_ref": sub.get("id", task_id),
                        "depth": sub_depth,
                        "global_remaining": remaining,
                        "reason": "max_total_fix_attempts",
                    })
                    child_nodes.append(ClosureNode(
                        task_id=sub.get("id", "?"),
                        depth=sub_depth,
                        outcome="recovery_exhausted",
                    ))
                    break

                sub_final, sub_node = _recurse(sub, sub_depth)
                total_attempts += sub_node.attempts_used
                child_nodes.append(sub_node)
                if sub_node.outcome != "resolved":
                    outcome = sub_node.outcome

        elif spawned and remaining <= 0:
            outcome = "recovery_exhausted"
            remaining -= 1
            _audit("recovery_exhausted", {
                "op": "recovery_exhausted",
                "task_ref": task_id,
                "depth": depth,
                "global_remaining": remaining,
                "reason": "max_total_fix_attempts",
            })

        else:
            outcome = halt_type or "blocked"

        node = ClosureNode(
            task_id=task_id,
            depth=depth,
            outcome=outcome,
            spawned_subtasks=len(spawned),
            attempts_used=total_attempts,
            subtasks=child_nodes,
        )
        return final, node

    root_task_dict = root_task if isinstance(root_task, dict) else {"id": str(root_task)}
    return _recurse(root_task_dict, depth=root_task_dict.get("depth", 0))

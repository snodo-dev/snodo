"""Engine-level tests for .snodo/ mutation by in-place coders (Fixes #52).

An in-place-writing coder (opencode and similar) writes to the working tree
directly and never goes through WorkspaceMCP, so the .snodo/ boundary is
enforced by the adapter base class. A post-run .snodo/ mutation raises
SnodoMutationError, which the engine surfaces as a terminal blocker halt and
records in the audit trail.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from snodo.compiler.models import DisagreementPolicy, Mode, Protocol, Validator
from snodo.core.interfaces import Task, TaskSpec, CodeArtifact, ValidatorResult
from snodo.engine.closure import run_to_closure
from snodo.engine.loop import build_protocol_graph
from snodo.coders.base import InPlaceCoderAdapter, SnodoMutationError


class _SnodoMutatingCoder(InPlaceCoderAdapter):
    """In-place coder that also writes under .snodo/."""

    def __init__(self, workspace: Path):
        self._workspace = workspace

    def _implement_in_place(self, spec: TaskSpec) -> CodeArtifact:
        (self._workspace / ".snodo" / "protocol.yml").write_text("mutated: true")
        (self._workspace / "app.py").write_text("print('ok')")
        return CodeArtifact(files=[])


def _protocol():
    return Protocol(
        protocol_id="snodo-protect",
        name="Snodo Protect",
        version="1.0.0",
        modes=[
            Mode(
                mode_id="producer",
                name="Producer",
                tools=["edit"],
                validators=["v1"],
            )
        ],
        validators=[
            Validator(validator_id="v1", validator_type="security",
                      criteria=["ok"]),
        ],
        disagreement_policy=DisagreementPolicy.UNANIMOUS,
        initial_mode="producer",
    )


def test_snodo_mutation_halts_blocker_with_audit(tmp_path):
    """A .snodo/ mutation by an in-place coder is a terminal blocker halt."""
    root = tmp_path / "proj"
    root.mkdir()
    snodo_dir = root / ".snodo"
    snodo_dir.mkdir()
    (snodo_dir / "protocol.yml").write_text("name: Test\n")

    audit = MagicMock()
    coder = _SnodoMutatingCoder(root)

    graph = build_protocol_graph(
        _protocol(),
        project_root=str(root),
        use_mock_coder=False,
        coder=coder,
        audit_log=audit,
        validator_fn=lambda task, validators, shell, **kwargs: [
            ValidatorResult(validator_id=v.validator_id, severity="pass",
                            justification="ok")
            for v in validators
        ],
    ).compile()

    task = Task(id="task_x", spec="do the thing")
    _final, tree = run_to_closure(graph, task, mode="producer")

    assert tree.outcome == "blocked"
    payload = tree.halt_payload
    assert payload is not None
    assert payload["halt_type"] == "blocker"
    assert payload["final_decision"] == "blocker"
    assert payload["status"] == "blocked"
    # post-validation is skipped, never a green verdict on the mutated tree
    assert payload["post_validation"]["outcome"] == "skipped"
    assert ".snodo/protocol.yml" in payload["reason"]

    # The mutation is recorded in the audit trail, not silently absent.
    mutation_events = [
        c.args[0] for c in audit.append_event.call_args_list
        if c.args and c.args[0] == "snodo_mutation_blocked"
    ]
    assert mutation_events, "no snodo_mutation_blocked audit event recorded"
    data = audit.append_event.call_args_list[
        [c.args[0] for c in audit.append_event.call_args_list].index("snodo_mutation_blocked")
    ].args[1]
    assert data["task_ref"] == "task_x"
    assert data["mode"] == "producer"
    assert ".snodo/protocol.yml" in data["paths"]


def test_snodo_mutation_leaves_tree_for_inspection(tmp_path):
    """The mutation is not undone — the tree is left for operator inspection."""
    root = tmp_path / "proj"
    root.mkdir()
    snodo_dir = root / ".snodo"
    snodo_dir.mkdir()
    (snodo_dir / "protocol.yml").write_text("name: Test\n")

    coder = _SnodoMutatingCoder(root)
    with pytest.raises(SnodoMutationError):
        coder.implement(TaskSpec(description="t", constraints=[]))

    # Not reverted: the evidence stays on disk.
    assert (root / ".snodo" / "protocol.yml").read_text() == "mutated: true"

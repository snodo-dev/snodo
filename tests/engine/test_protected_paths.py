"""Tests for protocol-declared protected-path diff detection."""

from snodo.compiler.models import Mode, Protocol, Validator
from snodo.core.interfaces import Task
from snodo.engine.nodes.validation import ValidationNodeMixin
from snodo.engine.state import LoopState


class _Git:
    def __init__(self, changed):
        self.changed = changed

    def get_head_sha(self):
        return "head"

    def changed_paths_between_refs(self, base, head):
        assert (base, head) == ("base", "head")
        return self.changed


class _Builder(ValidationNodeMixin):
    def __init__(self, protected, changed):
        self.protocol = Protocol(
            protocol_id="p",
            name="p",
            modes=[Mode(mode_id="m", name="m")],
            validators=[Validator(validator_id="v", validator_type="quality")],
            initial_mode="m",
            protected_paths=protected,
        )
        self.git_mcp = _Git(changed)
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

"""Tests for protocol-declared protected-path diff detection."""

from snodo.compiler.models import Mode, Protocol, Validator
from snodo.core.interfaces import Task
from snodo.engine.nodes.validation import ValidationNodeMixin
from snodo.engine.state import LoopState


class _Git:
    def __init__(self, changed, ancestor=None, raises=False):
        self.changed = changed
        self.ancestor = ancestor
        self.raises = raises
        self.expected = ("base", "head")
        self.repo = self

    def commit(self, ref):
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
    def __init__(self, protected, changed, ancestor=None, raises=False):
        self.protocol = Protocol(
            protocol_id="p",
            name="p",
            modes=[Mode(mode_id="m", name="m")],
            validators=[Validator(validator_id="v", validator_type="quality")],
            initial_mode="m",
            protected_paths=protected,
        )
        self.git_mcp = _Git(changed, ancestor, raises)
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


def test_merge_base_error_is_audited_without_blocker():
    builder = _Builder(["docs"], ["docs/policy.md"], raises=True)
    assert builder._protected_path_result(_state()) is None
    assert builder.audit_events[0][0] == "protected_paths_unchecked"

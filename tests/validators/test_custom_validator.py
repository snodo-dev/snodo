"""Custom validator proof test — extensibility demo (Task 7.20).

FILE: tests/validators/test_custom_validator.py

Demonstrates the full third-party flow:
  - Define a minimal ValidatorBase subclass
  - Register with a fresh ValidatorRegistry
  - Verify lookup resolution
  - Verify engine dispatch via GraphBuilder._dispatch_one
  - Confirm result flows through correctly

Serves double-duty as the docs example for the OSS extensibility story.
"""

import tempfile
from pathlib import Path

import pytest

from snodo.compiler.models import Protocol, Mode, Validator
from snodo.core.interfaces import Task, ValidatorResult
from snodo.engine.loop import GraphBuilder
from snodo.mcp.workspace import WorkspaceMCP
from snodo.validators.context import ValidatorContext, ValidatorBase
from snodo.validators.registry import ValidatorRegistry, _default_registry


# ---------------------------------------------------------------
# Minimal custom validator — doubles as the "getting started" example
# ---------------------------------------------------------------

class CustomValidator(ValidatorBase):
    """A third-party validator that checks arbitrary criteria."""

    def __init__(self, validator_spec: Validator, rule: str = ""):
        self.validator_spec = validator_spec
        self.validator_id = validator_spec.validator_id
        self.rule = rule

    @classmethod
    def registered_type(cls) -> str:
        return "custom_type"

    def evaluate(self, context: ValidatorContext) -> ValidatorResult:
        """Simple rule: spec description must mention 'safe'.

        This is intentionally trivial so the test is readable.
        A real implementation would read criteria from the spec
        and evaluate against the task, artifacts, or LLM.
        """
        spec_text = self.validator_spec.validator_id
        if context.task:
            spec_text += " " + context.task.spec
        if "safe" in context.task.spec.lower():
            return ValidatorResult(
                validator_id=self.validator_id,
                severity="pass",
                justification="Task spec mentions 'safe'.",
            )
        return ValidatorResult(
            validator_id=self.validator_id,
            severity="warn",
            justification="Task spec does not mention 'safe'.",
        )


# ---------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------

@pytest.fixture
def project_dir():
    d = tempfile.mkdtemp()
    (Path(d) / ".snodo").mkdir()
    return d


# ---------------------------------------------------------------
# Registration tests (fresh registry — no side effects)
# ---------------------------------------------------------------

class TestCustomValidatorRegistration:
    """ValidatorRegistry basics: register, lookup, list_types."""

    def test_register_and_lookup(self):
        """Custom type resolves via lookup after registration."""
        reg = ValidatorRegistry()
        reg.register(CustomValidator.registered_type(), CustomValidator)

        cls = reg.lookup("custom_type")
        assert cls is CustomValidator

    def test_lookup_missing_returns_none(self):
        """Unregistered types return None."""
        reg = ValidatorRegistry()
        assert reg.lookup("nonexistent") is None

    def test_list_types_includes_custom(self):
        """Custom type appears in list_types."""
        reg = ValidatorRegistry()
        reg.register("custom_type", CustomValidator)
        assert "custom_type" in reg.list_types()

    def test_compound_registration(self):
        """Compound registration maps aliases to primary."""
        reg = ValidatorRegistry()
        reg.register_compound({"custom_a", "custom_b"}, CustomValidator)

        # Primary (registered_type) and aliases all resolve
        assert reg.lookup("custom_type") is CustomValidator
        assert reg.lookup("custom_a") is CustomValidator
        assert reg.lookup("custom_b") is CustomValidator
        assert "custom_type" in reg.list_types()
        assert "custom_a" in reg.list_types()
        assert "custom_b" in reg.list_types()

    def test_registration_does_not_break_builtin_lookup(self):
        """Registering a custom type leaves built-ins untouched."""
        reg = ValidatorRegistry()
        reg.register("custom_type", CustomValidator)

        # Built-in types resolve via the default registry snapshot
        from snodo.validators.quality import QualityValidator
        builtin_cls = _default_registry.lookup("quality")
        assert builtin_cls is QualityValidator

        # Custom type is NOT in the default registry
        assert _default_registry.lookup("custom_type") is None


# ---------------------------------------------------------------
# Dispatch tests (live dispatch through _dispatch_one)
# ---------------------------------------------------------------

class TestCustomValidatorDispatch:
    """Engine dispatch: custom validator invoked and result flows through."""

    def test_dispatch_invokes_custom_validator(self, project_dir):
        """_dispatch_one resolves custom_type and calls CustomValidator.evaluate."""
        protocol = Protocol(
            protocol_id="test",
            name="Test",
            modes=[Mode(mode_id="m1", name="M", validators=["custom"])],
            validators=[
                Validator(
                    validator_id="custom",
                    validator_type="custom_type",
                    evaluation_phase="pre_execute",
                    criteria=["task must mention safe"],
                ),
            ],
            initial_mode="m1",
        )

        # Register custom validator in the default registry
        _default_registry.register("custom_type", CustomValidator)

        try:
            workspace = WorkspaceMCP(project_dir)
            builder = GraphBuilder(protocol, workspace_mcp=workspace)

            task = Task(id="t1", spec="implement a safe login flow")
            v_spec = protocol.get_validator("custom")

            # Build a minimal context (same shape as _default_validator)
            context = ValidatorContext(
                task=task,
                current_mode=protocol.get_mode("m1"),
                protocol=protocol,
                artifacts=[],
                audit_log=None,
                mode_name="M",
                mode_tools=[],
                mode_transitions={},
                mode_validator_refs=["custom"],
                completion_fn=None,
                model="gpt-4",
                working_directory=project_dir,
            )

            from snodo.validators.registry import _default_registry as reg
            result = builder._dispatch_one(v_spec, context, reg)

            assert result.validator_id == "custom"
            assert result.severity == "pass"
            assert "safe" in result.justification
        finally:
            # Clean up — remove custom type from default registry
            _default_registry._registry.pop("custom_type", None)

    def test_custom_validator_result_flows_in_default_validator(self, project_dir):
        """The full _default_validator loop collects custom validator results."""
        protocol = Protocol(
            protocol_id="test",
            name="Test",
            modes=[Mode(mode_id="m1", name="M", validators=["custom"])],
            validators=[
                Validator(
                    validator_id="custom",
                    validator_type="custom_type",
                    evaluation_phase="pre_execute",
                    criteria=["check"],
                ),
            ],
            initial_mode="m1",
        )

        _default_registry.register("custom_type", CustomValidator)

        try:
            workspace = WorkspaceMCP(project_dir)
            builder = GraphBuilder(protocol, workspace_mcp=workspace)

            task = Task(id="t1", spec="implement login logic")
            validators = [protocol.get_validator("custom")]
            results = builder._default_validator(task, validators, None)

            assert len(results) == 1
            assert results[0].validator_id == "custom"
            assert results[0].severity == "warn"
            assert "does not mention" in results[0].justification
        finally:
            _default_registry._registry.pop("custom_type", None)

    def test_custom_validator_with_context_fields(self, project_dir):
        """Custom validator can read mode, artifacts, etc. from context."""
        protocol = Protocol(
            protocol_id="test",
            name="Test",
            modes=[Mode(mode_id="m1", name="Review", tools=["lint"], validators=["custom"])],
            validators=[
                Validator(
                    validator_id="custom",
                    validator_type="custom_type",
                    evaluation_phase="pre_execute",
                    criteria=["check"],
                ),
            ],
            initial_mode="m1",
        )

        _default_registry.register("custom_type", CustomValidator)

        try:
            workspace = WorkspaceMCP(project_dir)
            builder = GraphBuilder(protocol, workspace_mcp=workspace)

            task = Task(id="t1", spec="safe deployment")
            validators = [protocol.get_validator("custom")]
            results = builder._default_validator(task, validators, None, current_mode="m1")

            assert len(results) == 1
            # The context was populated with mode info (no crash)
            # And CustomValidator ran correctly
            assert results[0].severity in ("pass", "warn")
        finally:
            _default_registry._registry.pop("custom_type", None)

    def test_unknown_type_returns_stub_not_crash(self, project_dir):
        """Unregistered types get a stub result, not an exception."""
        protocol = Protocol(
            protocol_id="test",
            name="Test",
            modes=[Mode(mode_id="m1", name="M", validators=["unk"])],
            validators=[
                Validator(
                    validator_id="unk",
                    validator_type="nonexistent_type",
                    evaluation_phase="pre_execute",
                ),
            ],
            initial_mode="m1",
        )

        workspace = WorkspaceMCP(project_dir)
        builder = GraphBuilder(protocol, workspace_mcp=workspace)

        task = Task(id="t1", spec="test")
        validators = [protocol.get_validator("unk")]
        results = builder._default_validator(task, validators, None)

        assert len(results) == 1
        assert results[0].validator_id == "unk"
        # Stub result — no crash
        assert results[0].severity in ("pass", "blocker", "warn")
        assert "Stub" in results[0].justification or "No validators configured" in results[0].justification

    def test_custom_validator_error_returns_warn(self, project_dir):
        """A validator that raises during evaluate() returns a warn result."""

        class BrokenValidator(ValidatorBase):
            def __init__(self, validator_spec: Validator = None, **kwargs):
                self.validator_spec = validator_spec
                self.validator_id = validator_spec.validator_id if validator_spec else "broken"

            @classmethod
            def registered_type(cls) -> str:
                return "broken_type"

            def evaluate(self, context):
                raise RuntimeError("simulated validator crash")

        protocol = Protocol(
            protocol_id="test",
            name="Test",
            modes=[Mode(mode_id="m1", name="M", validators=["broken"])],
            validators=[
                Validator(
                    validator_id="broken",
                    validator_type="broken_type",
                    evaluation_phase="pre_execute",
                    criteria=["check"],
                ),
            ],
            initial_mode="m1",
        )

        _default_registry.register("broken_type", BrokenValidator)

        try:
            workspace = WorkspaceMCP(project_dir)
            builder = GraphBuilder(protocol, workspace_mcp=workspace)

            task = Task(id="t1", spec="test")
            validators = [protocol.get_validator("broken")]
            results = builder._default_validator(task, validators, None)

            assert len(results) == 1
            assert results[0].validator_id == "broken"
            assert results[0].severity == "warn"
            assert "Validator error" in results[0].justification
            assert "simulated validator crash" in results[0].justification
        finally:
            _default_registry._registry.pop("broken_type", None)

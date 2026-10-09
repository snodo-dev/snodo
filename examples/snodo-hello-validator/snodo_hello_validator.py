"""A tiny, deterministic validator demonstrating Snodo's plugin interface."""

from snodo.core.interfaces import ValidatorResult
from snodo.validators.context import ValidatorBase, ValidatorContext


class HelloAcceptanceValidator(ValidatorBase):
    """Warn when a task specification omits an ACCEPTANCE section."""

    def __init__(self, validator_spec):
        # The runner instantiates validator classes with this keyword argument.
        self.validator_id = validator_spec.validator_id

    @classmethod
    def registered_type(cls) -> str:
        """Match the entry-point name used as validator_type in a protocol."""
        return "hello_acceptance"

    def evaluate(self, context: ValidatorContext) -> ValidatorResult:
        """Return pass when a standalone acceptance heading is present."""
        has_acceptance = any(
            line.strip().lstrip("#").strip().rstrip(":").casefold() == "acceptance"
            for line in context.task.spec.splitlines()
        )
        if has_acceptance:
            return ValidatorResult(
                validator_id=self.validator_id,
                severity="pass",
                justification="Task spec contains an ACCEPTANCE section.",
            )
        return ValidatorResult(
            validator_id=self.validator_id,
            severity="warn",
            justification="Task spec has no ACCEPTANCE section.",
        )

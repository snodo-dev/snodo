from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional

from snodo.core.interfaces import ValidatorResult
from snodo.engine.loop import LoopStage


@dataclass
class GateOutcome:
    """Aggregated outcome of running the verification gate.

    This is intentionally small and decoupled from any particular
    validator implementation so it can be consumed by CI wrappers,
    CLI commands, or higher‑level orchestration without pulling in
    the full engine or protocol graph.
    """

    severity: str
    """Highest severity observed across all validators.

    Mirrors :class:`snodo.core.interfaces.ValidatorResult` severities
    (e.g. ``"pass"``, ``"warn"`, ``"blocker"``).
    """

    error: bool
    """True if any validator reported an operational error.

    This is distinct from a failing quality/security signal; the
    gate may choose to handle operational errors differently in CI
    (for example, surfacing a flake vs. a real regression).
    """

    results: List[ValidatorResult]
    """Per‑validator results that contributed to this outcome."""


class VerificationGate:
    """Thin façade for executing the verification stage of a run.

    The gate is deliberately small and orchestrates only the
    *aggregation* of validator results. Construction of the
    underlying protocol graph and execution of individual validators
    is delegated to the engine layer to keep responsibilities
    separated and coupling low.
    """

    def __init__(self, *, loop_stage: LoopStage) -> None:
        self._loop_stage = loop_stage

    def evaluate(self) -> GateOutcome:
        """Run all validators for the current stage and aggregate.

        The gate does not interpret individual validator contracts
        beyond the shared :class:`ValidatorResult` surface. This keeps
        the dependency direction one‑way (gate → engine) and avoids
        the gate needing to understand domain‑specific behaviour such
        as flake detection or timeout handling, which live with the
        validators themselves.
        """

        results: List[ValidatorResult] = list(self._loop_stage.run())
        severity = self._max_severity(results)
        error = any(r.error for r in results)
        return GateOutcome(severity=severity, error=error, results=results)

    @staticmethod
    def _max_severity(results: Iterable[ValidatorResult]) -> str:
        """Return the highest severity across *results*.

        The ordering is defined in one place so that any future
        severities added to :class:`ValidatorResult` can be
        incorporated without changing call sites. If no results are
        present the gate is effectively a no‑op and the outcome is a
        pass.
        """

        order = {"pass": 0, "warn": 1, "blocker": 2}
        max_level = 0
        max_name: Optional[str] = "pass"
        for r in results:
            level = order.get(r.severity, 2)
            if level >= max_level:
                max_level = level
                max_name = r.severity
        return max_name or "pass"

"""Disagreement Policy Evaluator for validator consensus.

FILE: snodo/engine/policy.py

Takes N validator results and applies disagreement policy to determine
the appropriate action. Pure logic - no LLM calls.

Section 4.1 DisagreementPolicy implementation:
- UNANIMOUS: All validators must pass
- MAJORITY: >50% must pass  
- QUORUM: Configurable threshold (default 2/3)
- ANY: At least one must pass

Thresh holds on pass_count (not pass+warn).  Warn withholds approval —
it represents a validator that didn't pass, so it does not count toward
the threshold.  Policies differ on how many pass votes they require;
warn affects only the PROCEED_WITH_LOG sub-classification.

Blocker → HALT is a hard invariant across all policies (tested
before the policy dispatch).
"""

from enum import Enum
from typing import List, Optional, Any, Dict
from dataclasses import dataclass, asdict, is_dataclass

from snodo.core.interfaces import ValidatorResult
from snodo.compiler.models import DisagreementPolicy


class PolicyAction(str, Enum):
    """Action to take based on policy evaluation."""
    PROCEED = "proceed"              # All clear, continue
    PROCEED_WITH_LOG = "log"         # Continue but log warnings
    ESCALATE = "escalate"            # Require human decision
    HALT = "halt"                    # Hard stop, blockers present


@dataclass
class PolicyDecision:
    """Result of policy evaluation."""
    action: PolicyAction
    consensus_achieved: bool
    pass_count: int
    warn_count: int
    blocker_count: int
    total_count: int
    justification: str


def policy_decision_to_dict(pd: Any) -> Optional[Dict[str, Any]]:
    """Serialize a PolicyDecision to a checkpoint-safe dict.

    Returns a dict with string keys or None.
    Handles None, already-dict, live dataclass, and fallback.
    """
    if pd is None:
        return None
    if isinstance(pd, dict):
        return pd
    if is_dataclass(pd) and not isinstance(pd, type):
        return asdict(pd)
    return {"value": str(pd)}


class PolicyEvaluator:
    """Evaluates validator results against disagreement policies.

    Pure logic implementation - deterministic and testable.
    """

    _POLICY_DISPATCH = {
        DisagreementPolicy.UNANIMOUS: "_evaluate_unanimous",
        DisagreementPolicy.MAJORITY: "_evaluate_majority",
        DisagreementPolicy.QUORUM: "_evaluate_quorum",
        DisagreementPolicy.ANY: "_evaluate_any",
    }

    def __init__(self, quorum_threshold: float = 0.67):
        """Initialize policy evaluator.
        
        Args:
            quorum_threshold: Fraction of validators required for QUORUM policy (0.0-1.0)
        """
        if not 0.0 <= quorum_threshold <= 1.0:
            raise ValueError("quorum_threshold must be between 0.0 and 1.0")
        
        self.quorum_threshold = quorum_threshold
    
    def evaluate(
        self,
        results: List[ValidatorResult],
        policy: DisagreementPolicy
    ) -> PolicyDecision:
        """Evaluate validator results against policy.
        
        Args:
            results: List of validator results
            policy: Disagreement policy to apply
            
        Returns:
            PolicyDecision with action and justification
        """
        if not results:
            return PolicyDecision(
                action=PolicyAction.HALT,
                consensus_achieved=False,
                pass_count=0,
                warn_count=0,
                blocker_count=0,
                total_count=0,
                justification="No validator results provided"
            )
        
        # Count severities
        pass_count = sum(1 for r in results if r.severity == "pass")
        warn_count = sum(1 for r in results if r.severity == "warn")
        blocker_count = sum(1 for r in results if r.severity == "blocker")
        error_count = sum(1 for r in results if r.severity == "error")
        total_count = len(results)

        # Validator error always halts fail-closed (hard invariant)
        if error_count > 0:
            return PolicyDecision(
                action=PolicyAction.HALT,
                consensus_achieved=False,
                pass_count=pass_count,
                warn_count=warn_count,
                blocker_count=blocker_count,
                total_count=total_count,
                justification=f"{error_count} validator(s) failed to produce a verdict — fail-closed"
            )

        # Any blocker always halts (hard invariant)
        if blocker_count > 0:
            return PolicyDecision(
                action=PolicyAction.HALT,
                consensus_achieved=False,
                pass_count=pass_count,
                warn_count=warn_count,
                blocker_count=blocker_count,
                total_count=total_count,
                justification=f"{blocker_count} blocker(s) present"
            )
        
        # Apply policy to non-blocker results via dispatch
        evaluator = self._POLICY_DISPATCH.get(policy)
        if not evaluator:
            raise ValueError(f"Unknown policy: {policy}")
        return getattr(self, evaluator)(
            pass_count, warn_count, blocker_count, total_count
        )
    
    def _evaluate_unanimous(
        self,
        pass_count: int,
        warn_count: int,
        blocker_count: int,
        total_count: int
    ) -> PolicyDecision:
        """All validators must pass (threshold on pass_count)."""
        if pass_count == total_count:
            if warn_count > 0:
                return PolicyDecision(
                    action=PolicyAction.PROCEED_WITH_LOG,
                    consensus_achieved=True,
                    pass_count=pass_count,
                    warn_count=warn_count,
                    blocker_count=blocker_count,
                    total_count=total_count,
                    justification=f"Unanimous approval ({pass_count}/{total_count}) with {warn_count} warning(s)"
                )
            else:
                return PolicyDecision(
                    action=PolicyAction.PROCEED,
                    consensus_achieved=True,
                    pass_count=pass_count,
                    warn_count=warn_count,
                    blocker_count=blocker_count,
                    total_count=total_count,
                    justification="Unanimous pass"
                )
        else:
            return PolicyDecision(
                action=PolicyAction.ESCALATE,
                consensus_achieved=False,
                pass_count=pass_count,
                warn_count=warn_count,
                blocker_count=blocker_count,
                total_count=total_count,
                justification="Unanimous policy requires all validators to pass"
            )
    
    def _evaluate_majority(
        self,
        pass_count: int,
        warn_count: int,
        blocker_count: int,
        total_count: int
    ) -> PolicyDecision:
        """>50% must pass (warn does not count)."""
        required = total_count / 2.0
        
        if pass_count > required:
            if warn_count > 0:
                return PolicyDecision(
                    action=PolicyAction.PROCEED_WITH_LOG,
                    consensus_achieved=True,
                    pass_count=pass_count,
                    warn_count=warn_count,
                    blocker_count=blocker_count,
                    total_count=total_count,
                    justification=f"Majority pass ({pass_count}/{total_count}) with {warn_count} warning(s)"
                )
            else:
                return PolicyDecision(
                    action=PolicyAction.PROCEED,
                    consensus_achieved=True,
                    pass_count=pass_count,
                    warn_count=warn_count,
                    blocker_count=blocker_count,
                    total_count=total_count,
                    justification=f"Majority pass ({pass_count}/{total_count})"
                )
        else:
            return PolicyDecision(
                action=PolicyAction.ESCALATE,
                consensus_achieved=False,
                pass_count=pass_count,
                warn_count=warn_count,
                blocker_count=blocker_count,
                total_count=total_count,
                justification=f"Majority not achieved ({pass_count}/{total_count}, need >{required})"
            )
    
    def _evaluate_quorum(
        self,
        pass_count: int,
        warn_count: int,
        blocker_count: int,
        total_count: int
    ) -> PolicyDecision:
        """Configurable threshold on pass_count (default 2/3)."""
        required = total_count * self.quorum_threshold
        
        if pass_count >= required:
            if warn_count > 0:
                return PolicyDecision(
                    action=PolicyAction.PROCEED_WITH_LOG,
                    consensus_achieved=True,
                    pass_count=pass_count,
                    warn_count=warn_count,
                    blocker_count=blocker_count,
                    total_count=total_count,
                    justification=f"Quorum achieved ({pass_count}/{total_count}, need >={required:.1f}) with {warn_count} warning(s)"
                )
            else:
                return PolicyDecision(
                    action=PolicyAction.PROCEED,
                    consensus_achieved=True,
                    pass_count=pass_count,
                    warn_count=warn_count,
                    blocker_count=blocker_count,
                    total_count=total_count,
                    justification=f"Quorum pass ({pass_count}/{total_count}, need >={required:.1f})"
                )
        else:
            return PolicyDecision(
                action=PolicyAction.ESCALATE,
                consensus_achieved=False,
                pass_count=pass_count,
                warn_count=warn_count,
                blocker_count=blocker_count,
                total_count=total_count,
                justification=f"Quorum not achieved ({pass_count}/{total_count}, need >={required:.1f})"
            )
    
    def _evaluate_any(
        self,
        pass_count: int,
        warn_count: int,
        blocker_count: int,
        total_count: int
    ) -> PolicyDecision:
        """At least one must pass (warn does not count)."""
        
        if pass_count >= 1:
            if warn_count > 0:
                return PolicyDecision(
                    action=PolicyAction.PROCEED_WITH_LOG,
                    consensus_achieved=True,
                    pass_count=pass_count,
                    warn_count=warn_count,
                    blocker_count=blocker_count,
                    total_count=total_count,
                    justification=f"At least one pass ({pass_count}/{total_count}) with {warn_count} warning(s)"
                )
            else:
                return PolicyDecision(
                    action=PolicyAction.PROCEED,
                    consensus_achieved=True,
                    pass_count=pass_count,
                    warn_count=warn_count,
                    blocker_count=blocker_count,
                    total_count=total_count,
                    justification=f"At least one pass ({pass_count}/{total_count})"
                )
        else:
            return PolicyDecision(
                action=PolicyAction.ESCALATE,
                consensus_achieved=False,
                pass_count=pass_count,
                warn_count=warn_count,
                blocker_count=blocker_count,
                total_count=total_count,
                justification="No validators passed"
            )


# Convenience function
def evaluate_policy(
    results: List[ValidatorResult],
    policy: DisagreementPolicy,
    quorum_threshold: float = 0.67
) -> PolicyDecision:
    """Evaluate policy (convenience function).
    
    Args:
        results: Validator results
        policy: Disagreement policy
        quorum_threshold: Threshold for QUORUM policy
        
    Returns:
        PolicyDecision
    """
    evaluator = PolicyEvaluator(quorum_threshold=quorum_threshold)
    return evaluator.evaluate(results, policy)

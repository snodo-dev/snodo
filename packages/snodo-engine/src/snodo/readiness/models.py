"""Data models for Protocol-Scaffolding Readiness assessment.

FILE: snodo/readiness/models.py

Readiness is a property of the method scaffolding relative to the configured
protocol, never of the codebase.

Two distinct categories of readiness exist:
1. Repository / Tree Readiness (SCORED): What lives in the repository and travels
   with git across every machine (committed decision records, resolvable test
   command, coder config files, paths cited by criteria).
2. Workstation Readiness (REPORTED, UNSCORED): What lives on the operator's machine
   (binaries on PATH, environment variables, API credentials).
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class ReadinessKind(str, Enum):
    """Category of readiness check."""
    REPOSITORY = "repository"    # Scored: travels with git repository
    WORKSTATION = "workstation"  # Unscored: workstation environment specific


class FindingSeverity(str, Enum):
    """Severity of a readiness finding."""
    BLOCKER = "blocker"
    WARN = "warn"
    INFO = "info"

    def weight(self) -> int:
        _weights = {"blocker": 3, "warn": 2, "info": 1}
        return _weights.get(self.value, 0)


@dataclass(frozen=True)
class ReadinessFinding:
    """A single finding identified during readiness assessment."""
    id: str
    kind: ReadinessKind
    severity: FindingSeverity
    modes: List[str]
    description: str
    remediation: str
    fix_cost: int = 2  # 1=trivial/git-add, 2=quick/file, 3=config/install, 4=structural

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "severity": self.severity.value,
            "modes": self.modes,
            "finding": self.description,
            "remediation": self.remediation,
            "fix_cost": self.fix_cost,
        }


@dataclass
class ReadinessAssessment:
    """Complete readiness assessment for a project and its protocol."""
    protocol_id: str
    score: int  # 0 to 100 percentage for repository scaffolding readiness
    total_checks: int
    passed_checks: int
    repository_findings: List[ReadinessFinding] = field(default_factory=list)
    workstation_findings: List[ReadinessFinding] = field(default_factory=list)

    @property
    def all_findings(self) -> List[ReadinessFinding]:
        """All findings ordered by cheapest fix at highest severity first."""
        combined = self.repository_findings + self.workstation_findings
        return sorted(
            combined,
            key=lambda f: (-f.severity.weight(), f.fix_cost, f.id),
        )

    def findings_for_mode(self, mode: Optional[str] = None) -> List[ReadinessFinding]:
        """Return findings filtered by mode (or all if mode is None), keeping order."""
        if not mode:
            return self.all_findings
        return [
            f for f in self.all_findings
            if "all" in f.modes or mode in f.modes
        ]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "protocol_id": self.protocol_id,
            "score": self.score,
            "total_checks": self.total_checks,
            "passed_checks": self.passed_checks,
            "repository_findings_count": len(self.repository_findings),
            "workstation_findings_count": len(self.workstation_findings),
            "findings": [f.to_dict() for f in self.all_findings],
        }

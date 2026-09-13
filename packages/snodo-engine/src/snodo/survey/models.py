"""Data models for repository survey analysis.

FILE: snodo/survey/models.py

Survey discovers observable facts about an existing repository:
- Module boundaries (from workspace markers like package.json, pyproject.toml)
- Languages and tooling per module
- Test commands (from marker files or explicit configuration)
- Documentation and decision record locations
- Repository-level tooling and configuration

Judgements that need knowing-what-the-repository-is-for (is a manifest a
product or scaffolding? is a source-dense directory an undeclared boundary?)
are recorded with the evidence files they cite, and the judgements that were
not made are recorded too.

A governed repository is asked a different closing question — does this
protocol still describe this code? — answered by the drift types below. A
divergence names what the protocol claims and what the code shows and takes
no side; an agreement is reported as plainly as a divergence; and every
comparison the protocol's shape does not support is recorded as not made,
with the reason.

The survey never infers governance requirements from absence of practices.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class SurveyFinding:
    """An observable finding from repository analysis."""
    message: str
    evidence: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class JudgementRecord:
    """A judgement the agent made, attributable to gathered evidence.

    ``cited_files`` are repository-relative paths from the evidence dossier
    the deterministic pass gathered; a verdict citing anything else is not
    accepted (see analyzer._apply_verdict).
    """
    subject_id: str
    kind: str
    subject_path: str
    verdict: str
    reason: str
    cited_files: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "subject_id": self.subject_id,
            "kind": self.kind,
            "subject_path": self.subject_path,
            "verdict": self.verdict,
            "reason": self.reason,
            "cited_files": self.cited_files,
        }


@dataclass(frozen=True)
class UnmadeJudgement:
    """A judgement that was needed but not made, and why.

    The deterministic result stands in its place; the gap is reported
    plainly rather than guessed around.
    """
    subject_id: str
    kind: str
    subject_path: str
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "subject_id": self.subject_id,
            "kind": self.kind,
            "subject_path": self.subject_path,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class DriftFinding:
    """A point where what the protocol claims and what the code shows differ.

    ``claim`` restates the protocol and ``observation`` restates the
    repository; neither says which of the two moved, because the survey does
    not know and taking a side would make the report an argument rather than
    a measurement.
    """
    check: str
    subject: str
    claim: str
    observation: str
    evidence: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "check": self.check,
            "subject": self.subject,
            "claim": self.claim,
            "observation": self.observation,
            "evidence": self.evidence,
        }


@dataclass(frozen=True)
class DriftAgreement:
    """A comparison that was made and came back agreeing.

    Agreement is the majority result of a healthy governed repository and is
    reported for the same checks a divergence could come from, so the absence
    of a finding is a finding of its own rather than silence. ``subject`` names
    the single thing compared where there is one (a command, a module); a
    roll-up over several leaves it empty and carries the detail in evidence.
    """
    check: str
    statement: str
    subject: str = ""
    evidence: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "check": self.check,
            "statement": self.statement,
            "subject": self.subject,
            "evidence": self.evidence,
        }


@dataclass(frozen=True)
class ComparisonNotMade:
    """A comparison this protocol's shape does not support, and why.

    Recorded rather than approximated: a drift report is worth only what its
    findings are true, so a check that would produce noise on the common
    shape of protocol is left out and the omission is stated.
    """
    check: str
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return {"check": self.check, "reason": self.reason}


@dataclass(frozen=True)
class ProtocolDrift:
    """The relationship between a protocol's claims and the surveyed code."""
    protocol_id: str
    uses_modules: bool
    shape: str
    summary: str
    checks_made: int = 0
    agreements: List[DriftAgreement] = field(default_factory=list)
    divergences: List[DriftFinding] = field(default_factory=list)
    not_compared: List[ComparisonNotMade] = field(default_factory=list)

    @property
    def has_divergences(self) -> bool:
        return bool(self.divergences)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "protocol_id": self.protocol_id,
            "uses_modules": self.uses_modules,
            "shape": self.shape,
            "summary": self.summary,
            "checks_made": self.checks_made,
            "has_divergences": self.has_divergences,
            "agreements": [a.to_dict() for a in self.agreements],
            "divergences": [d.to_dict() for d in self.divergences],
            "not_compared": [n.to_dict() for n in self.not_compared],
        }


@dataclass(frozen=True)
class ModuleInfo:
    """Information about a discovered module."""
    module_id: str
    paths: List[str] = field(default_factory=list)
    languages: List[str] = field(default_factory=list)
    test_command: Optional[str] = None
    test_marker_file: Optional[str] = None
    decisions_path: Optional[str] = None
    evidence: List[str] = field(default_factory=list)
    origin: str = "manifest"  # "manifest" | "declaration" | "agent-judgement"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "module_id": self.module_id,
            "paths": self.paths,
            "languages": self.languages,
            "test_command": self.test_command,
            "test_marker_file": self.test_marker_file,
            "decisions_path": self.decisions_path,
            "evidence": self.evidence,
            "origin": self.origin,
        }


@dataclass
class RepositorySurvey:
    """Complete survey analysis of a repository."""
    modules: List[ModuleInfo] = field(default_factory=list)
    repository_tooling: Dict[str, str] = field(default_factory=dict)
    decision_paths: List[str] = field(default_factory=list)
    test_command: Optional[str] = None
    test_marker_file: Optional[str] = None
    languages: List[str] = field(default_factory=list)
    findings: List[SurveyFinding] = field(default_factory=list)
    judgements: List[JudgementRecord] = field(default_factory=list)
    unmade_judgements: List[UnmadeJudgement] = field(default_factory=list)
    agent_consulted: bool = False
    agent_model: Optional[str] = None
    drift: Optional[ProtocolDrift] = None

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "modules": [m.to_dict() for m in self.modules],
            "repository_tooling": self.repository_tooling,
            "decision_paths": self.decision_paths,
            "test_command": self.test_command,
            "test_marker_file": self.test_marker_file,
            "languages": self.languages,
            "findings": [{"message": f.message, "evidence": f.evidence} for f in self.findings],
            "judgements": [j.to_dict() for j in self.judgements],
            "unmade_judgements": [u.to_dict() for u in self.unmade_judgements],
            "agent_consulted": self.agent_consulted,
            "agent_model": self.agent_model,
        }
        # Absent, not null, on an ungoverned repository: that report is the
        # published baseline and a governed repository's extra field is
        # additive to it (ADR 022).
        if self.drift is not None:
            payload["drift"] = self.drift.to_dict()
        return payload

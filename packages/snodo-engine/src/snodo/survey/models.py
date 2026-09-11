"""Data models for repository survey analysis.

FILE: snodo/survey/models.py

Survey discovers observable facts about an existing repository:
- Module boundaries (from workspace markers like package.json, pyproject.toml)
- Languages and tooling per module
- Test commands (from marker files or explicit configuration)
- Documentation and decision record locations
- Repository-level tooling and configuration

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
class ModuleInfo:
    """Information about a discovered module."""
    module_id: str
    paths: List[str] = field(default_factory=list)
    languages: List[str] = field(default_factory=list)
    test_command: Optional[str] = None
    test_marker_file: Optional[str] = None
    decisions_path: Optional[str] = None
    evidence: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "module_id": self.module_id,
            "paths": self.paths,
            "languages": self.languages,
            "test_command": self.test_command,
            "test_marker_file": self.test_marker_file,
            "decisions_path": self.decisions_path,
            "evidence": self.evidence,
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

    def to_dict(self) -> Dict[str, Any]:
        return {
            "modules": [m.to_dict() for m in self.modules],
            "repository_tooling": self.repository_tooling,
            "decision_paths": self.decision_paths,
            "test_command": self.test_command,
            "test_marker_file": self.test_marker_file,
            "languages": self.languages,
            "findings": [{"message": f.message, "evidence": f.evidence} for f in self.findings],
        }

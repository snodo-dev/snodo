"""Repository survey module for analyzing existing codebases.

FILE: snodo/survey/__init__.py

Discovers observable facts about existing repositories:
- Module boundaries from workspace markers
- Languages and tooling
- Test commands and documentation locations
- Decision record organization

Judgements that need knowing what the repository is for are put to an
injected judge (the CLI wires it to the recon machinery); absent a judge,
the deterministic result stands and the unmade judgements are listed.

Never infers governance requirements from absence of practices.
"""

from snodo.survey.analyzer import analyze_repository
from snodo.survey.models import (
    JudgementRecord,
    ModuleInfo,
    RepositorySurvey,
    SurveyFinding,
    UnmadeJudgement,
)

__all__ = [
    "analyze_repository",
    "JudgementRecord",
    "ModuleInfo",
    "RepositorySurvey",
    "SurveyFinding",
    "UnmadeJudgement",
]

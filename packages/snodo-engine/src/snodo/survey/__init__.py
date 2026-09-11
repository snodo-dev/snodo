"""Repository survey module for analyzing existing codebases.

FILE: snodo/survey/__init__.py

Discovers observable facts about existing repositories:
- Module boundaries from workspace markers
- Languages and tooling
- Test commands and documentation locations
- Decision record organization

Never infers governance requirements from absence of practices.
"""

from snodo.survey.analyzer import analyze_repository
from snodo.survey.models import ModuleInfo, RepositorySurvey, SurveyFinding

__all__ = [
    "analyze_repository",
    "ModuleInfo",
    "RepositorySurvey",
    "SurveyFinding",
]

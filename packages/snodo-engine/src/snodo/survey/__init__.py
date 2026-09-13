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

For a repository that already has a protocol, `compare_protocol` adds the
governed question — does the protocol still describe the code? — reporting
agreement, divergence, and the comparisons this protocol's shape does not
support. It writes nothing and reconciles nothing.

Never infers governance requirements from absence of practices.
"""

from snodo.survey.analyzer import analyze_repository
from snodo.survey.drift import compare_protocol
from snodo.survey.measure import (
    CorpusMeasurement,
    ExpectedRepository,
    RepositoryMeasurement,
    Score,
    SurveyMeasurementError,
    discovered_boundaries,
    discovered_languages,
    load_ground_truth,
    measure_repository,
    render_measurement,
    reported_test_commands,
    score_sets,
    score_survey,
)
from snodo.survey.models import (
    ComparisonNotMade,
    DriftAgreement,
    DriftFinding,
    JudgementRecord,
    ModuleInfo,
    ProtocolDrift,
    RepositorySurvey,
    SurveyFinding,
    UnmadeJudgement,
)

__all__ = [
    "ComparisonNotMade",
    "DriftAgreement",
    "DriftFinding",
    "ProtocolDrift",
    "analyze_repository",
    "CorpusMeasurement",
    "ExpectedRepository",
    "JudgementRecord",
    "ModuleInfo",
    "RepositoryMeasurement",
    "RepositorySurvey",
    "Score",
    "SurveyFinding",
    "SurveyMeasurementError",
    "UnmadeJudgement",
    "compare_protocol",
    "discovered_boundaries",
    "discovered_languages",
    "load_ground_truth",
    "measure_repository",
    "render_measurement",
    "reported_test_commands",
    "score_sets",
    "score_survey",
]

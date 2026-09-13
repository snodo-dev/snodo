"""The instrument that turns a survey into precision and recall figures.

FILE: snodo/survey/measure.py

The survey's accuracy claim rests on two questions: did the pass find the
module boundaries and languages that are actually there (recall), and did it
propose boundaries and languages that are not (precision)? This module turns
a survey plus a declared ground truth into exactly those figures, so one
arithmetic backs the fixture corpus in the tests, the on-demand measurement
script, and any future re-run.

A repository's ground truth is a set of expected boundary paths and expected
languages. Boundaries are compared as repo-relative paths, languages as
language names. Precision and recall are computed over the aggregate of every
repository measured, so a false positive in one repository can never cancel a
true positive in another.

Nothing here calls a model. The agent-judged pass is measured by passing a
judge to :func:`measure_repository`; with no judge the deterministic result
is scored, which is how the two passes are compared.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, FrozenSet, Iterable, List, Optional

from snodo.survey.analyzer import analyze_repository
from snodo.survey.models import RepositorySurvey


def discovered_boundaries(survey: RepositorySurvey) -> FrozenSet[str]:
    """Boundary paths the survey concluded, one per module."""
    return frozenset(module.paths[0] for module in survey.modules if module.paths)


def discovered_languages(survey: RepositorySurvey) -> FrozenSet[str]:
    """Languages the survey reports, module-level and repository-level."""
    found = set(survey.languages)
    for module in survey.modules:
        found.update(module.languages)
    return frozenset(found)


def reported_test_commands(survey: RepositorySurvey) -> FrozenSet[str]:
    """Test commands the survey confirmed, repository-level and per module."""
    found = set()
    if survey.test_command:
        found.add(survey.test_command)
    for module in survey.modules:
        if module.test_command:
            found.add(module.test_command)
    return frozenset(found)


@dataclass(frozen=True)
class ExpectedRepository:
    """A repository's declared answer: the truth the survey is scored against.

    ``path`` is where the repository lives on disk, used by the measurement
    script; the fixture corpus builds its trees itself and leaves it unset.
    ``scaffolding`` and ``undeclared_modules`` are judgement ground truth,
    used only to replay a deterministic judge offline; a real measurement
    against an agent omits them.
    """

    name: str
    boundaries: FrozenSet[str] = frozenset()
    languages: FrozenSet[str] = frozenset()
    test_command: Optional[str] = None
    path: Optional[str] = None
    scaffolding: FrozenSet[str] = frozenset()
    undeclared_modules: FrozenSet[str] = frozenset()


@dataclass(frozen=True)
class Score:
    """True/false counts and the precision and recall derived from them."""

    true_positives: int
    false_positives: int
    false_negatives: int

    @property
    def precision(self) -> float:
        proposed = self.true_positives + self.false_positives
        return self.true_positives / proposed if proposed else 1.0

    @property
    def recall(self) -> float:
        known = self.true_positives + self.false_negatives
        return self.true_positives / known if known else 1.0

    def to_dict(self) -> dict:
        return {
            "true_positives": self.true_positives,
            "false_positives": self.false_positives,
            "false_negatives": self.false_negatives,
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
        }


def score_sets(expected: Iterable[str], discovered: Iterable[str]) -> Score:
    """Compare a declared set with what the survey found."""
    expected_set = set(expected)
    discovered_set = set(discovered)
    return Score(
        true_positives=len(expected_set & discovered_set),
        false_positives=len(discovered_set - expected_set),
        false_negatives=len(expected_set - discovered_set),
    )


def _add_scores(scores: Iterable[Score]) -> Score:
    collected = list(scores)
    return Score(
        true_positives=sum(s.true_positives for s in collected),
        false_positives=sum(s.false_positives for s in collected),
        false_negatives=sum(s.false_negatives for s in collected),
    )


@dataclass(frozen=True)
class RepositoryMeasurement:
    """One repository's survey, its expected answer, and the gap between them."""

    expected: ExpectedRepository
    survey: RepositorySurvey
    boundaries: FrozenSet[str]
    languages: FrozenSet[str]
    boundary_score: Score
    language_score: Score

    @property
    def missing_boundaries(self) -> FrozenSet[str]:
        return self.expected.boundaries - self.boundaries

    @property
    def unexpected_boundaries(self) -> FrozenSet[str]:
        return self.boundaries - self.expected.boundaries

    @property
    def missing_languages(self) -> FrozenSet[str]:
        return self.expected.languages - self.languages

    @property
    def unexpected_languages(self) -> FrozenSet[str]:
        return self.languages - self.expected.languages

    @property
    def test_command_ok(self) -> bool:
        """Whether the expected command is among those the report confirmed.

        A test command belongs to whichever package declares it, so it may be
        reported at the repository level (single package) or on a module. An
        expected ``None`` means no command should have been confirmed at all.
        """
        reported = reported_test_commands(self.survey)
        if self.expected.test_command is None:
            return not reported
        return self.expected.test_command in reported


def score_survey(
    expected: ExpectedRepository, survey: RepositorySurvey
) -> RepositoryMeasurement:
    """Score an already-computed survey against a declared answer."""
    boundaries = discovered_boundaries(survey)
    languages = discovered_languages(survey)
    return RepositoryMeasurement(
        expected=expected,
        survey=survey,
        boundaries=boundaries,
        languages=languages,
        boundary_score=score_sets(expected.boundaries, boundaries),
        language_score=score_sets(expected.languages, languages),
    )


def measure_repository(
    expected: ExpectedRepository,
    project_root: Path,
    judge: Optional[Callable] = None,
    judge_mode: str = "off",
) -> RepositoryMeasurement:
    """Run the survey over one repository and score it against its truth."""
    survey = analyze_repository(Path(project_root), judge=judge, judge_mode=judge_mode)
    return score_survey(expected, survey)


class SurveyMeasurementError(AssertionError):
    """The measured survey did not reproduce the declared ground truth."""


@dataclass
class CorpusMeasurement:
    """Aggregate scores across a corpus of measured repositories."""

    measurements: List[RepositoryMeasurement] = field(default_factory=list)

    @property
    def boundary_score(self) -> Score:
        return _add_scores(m.boundary_score for m in self.measurements)

    @property
    def language_score(self) -> Score:
        return _add_scores(m.language_score for m in self.measurements)

    def mismatches(self) -> List[str]:
        """Every place a repository's survey diverged from its declared answer.

        This is the full report, including an unexpected language (a
        precision gap). The claim the corpus asserts is narrower: boundary
        precision and recall, and language recall. See
        :meth:`unmet_expectations`.
        """
        problems: List[str] = []
        for m in self.measurements:
            if m.missing_boundaries:
                problems.append(
                    f"{m.expected.name}: missing boundaries "
                    f"{sorted(m.missing_boundaries)}"
                )
            if m.unexpected_boundaries:
                problems.append(
                    f"{m.expected.name}: unexpected boundaries "
                    f"{sorted(m.unexpected_boundaries)}"
                )
            if m.missing_languages:
                problems.append(
                    f"{m.expected.name}: missing languages "
                    f"{sorted(m.missing_languages)}"
                )
            if m.unexpected_languages:
                problems.append(
                    f"{m.expected.name}: unexpected languages "
                    f"{sorted(m.unexpected_languages)}"
                )
            if not m.test_command_ok:
                problems.append(
                    f"{m.expected.name}: test commands were "
                    f"{sorted(reported_test_commands(m.survey))}, expected "
                    f"{m.expected.test_command!r}"
                )
        return problems

    def unmet_expectations(self) -> List[str]:
        """Divergences that mean a declared answer was not met.

        The measurement makes three promises: every product boundary is
        found (boundary recall), no boundary is proposed that is not one
        (boundary precision), and every language the source is written in is
        found (language recall). An extra language is a language-precision
        gap the figures expose; it does not mean an expected answer was
        missed, so it is reported by :meth:`mismatches` rather than here.
        """
        problems: List[str] = []
        for m in self.measurements:
            if m.missing_boundaries:
                problems.append(
                    f"{m.expected.name}: missing boundaries "
                    f"{sorted(m.missing_boundaries)}"
                )
            if m.unexpected_boundaries:
                problems.append(
                    f"{m.expected.name}: unexpected boundaries "
                    f"{sorted(m.unexpected_boundaries)}"
                )
            if m.missing_languages:
                problems.append(
                    f"{m.expected.name}: missing languages "
                    f"{sorted(m.missing_languages)}"
                )
            if not m.test_command_ok:
                problems.append(
                    f"{m.expected.name}: test commands were "
                    f"{sorted(reported_test_commands(m.survey))}, expected "
                    f"{m.expected.test_command!r}"
                )
        return problems

    def assert_met(self) -> None:
        """Raise naming every declared answer the corpus did not meet."""
        problems = self.unmet_expectations()
        if problems:
            raise SurveyMeasurementError(
                "survey measurement did not reproduce the declared ground "
                "truth:\n  - " + "\n  - ".join(problems)
            )


def load_ground_truth(path: Path) -> List[ExpectedRepository]:
    """Read a ground-truth file naming repositories and their expected answers.

    The file is a JSON object with a ``repositories`` list (a bare list is
    also accepted). Each entry names a repository, a path (absolute, or
    relative to the file), and its expected boundaries and languages. The
    optional ``scaffolding`` and ``undeclared_modules`` fields replay an
    offline judge; a real agent measurement leaves them out.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    entries = data.get("repositories") if isinstance(data, dict) else data
    if not isinstance(entries, list):
        raise ValueError(
            "ground truth must be a list or an object with a 'repositories' list"
        )

    expected: List[ExpectedRepository] = []
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("name"):
            raise ValueError(f"ground truth entry is missing a name: {entry!r}")
        expected.append(
            ExpectedRepository(
                name=str(entry["name"]),
                path=entry.get("path"),
                boundaries=frozenset(entry.get("boundaries", [])),
                languages=frozenset(entry.get("languages", [])),
                test_command=entry.get("test_command"),
                scaffolding=frozenset(entry.get("scaffolding", [])),
                undeclared_modules=frozenset(entry.get("undeclared_modules", [])),
            )
        )
    return expected


def render_measurement(
    corpus: CorpusMeasurement, per_repository: bool = False
) -> str:
    """A human-readable rendering of the figures and any mismatches."""
    lines: List[str] = []
    if per_repository:
        for m in corpus.measurements:
            boundary, language = m.boundary_score, m.language_score
            lines.append(
                f"{m.expected.name}: boundaries P={boundary.precision:.0%} "
                f"R={boundary.recall:.0%} "
                f"(tp={boundary.true_positives} fp={boundary.false_positives} "
                f"fn={boundary.false_negatives}); languages "
                f"P={language.precision:.0%} R={language.recall:.0%}"
            )
        if corpus.measurements:
            lines.append("")

    boundary, language = corpus.boundary_score, corpus.language_score
    lines.append(
        f"Module boundaries: precision={boundary.precision:.0%} "
        f"recall={boundary.recall:.0%} "
        f"(tp={boundary.true_positives} fp={boundary.false_positives} "
        f"fn={boundary.false_negatives})"
    )
    lines.append(
        f"Languages: precision={language.precision:.0%} "
        f"recall={language.recall:.0%} "
        f"(tp={language.true_positives} fp={language.false_positives} "
        f"fn={language.false_negatives})"
    )

    problems = corpus.mismatches()
    if problems:
        lines.append("")
        lines.append("Mismatches against the declared ground truth:")
        lines.extend(f"  - {problem}" for problem in problems)
    return "\n".join(lines)

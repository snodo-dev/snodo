"""Tests for the protocol-versus-code comparison survey reports for a governed repository.

FILE: tests/survey/test_drift.py

Both protocol shapes are covered, because the one that declares no modules is
the common case and is easy to get wrong:

* a protocol that declares modules, whose paths, decision records and module
  tooling are all compared;
* a protocol that declares none, on which no discovered boundary may be
  reported as ungoverned — a protocol that does not use modules is not drifting
  from its code by not naming one.

Plus the discipline the drift report's value rests on: findings are true. A
protocol that matches its code is reported as agreeing; a validator's tooling
value that merely contains a slash is not a path; each subject is reported once
however many modules name it; and the report never implies which side moved.
"""

from pathlib import Path

import pytest
import yaml

from snodo.compiler.models import Protocol
from snodo.compiler.verifier import verify_protocol
from snodo.survey.analyzer import analyze_repository
from snodo.survey.drift import (
    CHECK_DECISION_RECORDS,
    CHECK_MODULE_COVERAGE,
    CHECK_MODULE_PATHS,
    CHECK_TEST_COMMANDS,
    CHECK_TOOLING_VALUES,
    compare_protocol,
)
from tests.survey.protocol_fixtures import protocol_body as _protocol_body


def _protocol(**kwargs) -> Protocol:
    protocol = Protocol(**_protocol_body(**kwargs))
    result = verify_protocol(protocol)
    assert result.passed, result.errors
    return protocol


def _survey(project_root: Path):
    return analyze_repository(project_root, judge=None, judge_mode="off")


def _write(root: Path, rel: str, text: str = "") -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text or f"{rel}\n")
    return path


@pytest.fixture
def monorepo(tmp_path: Path) -> Path:
    """A two-package repository with an ADR folder and one runner per package."""
    _write(tmp_path, "services/api/pyproject.toml",
           "[project]\nname='api'\n[tool.pytest.ini_options]\n")
    _write(tmp_path, "services/api/app.py", "x = 1\n")
    _write(tmp_path, "apps/web/package.json",
           '{"name": "web", "scripts": {"test": "vitest run"}}')
    _write(tmp_path, "apps/web/main.ts", "export const x = 1\n")
    _write(tmp_path, "docs/adr/001-shape.md", "# 001\n")
    return tmp_path


class TestAgreeingProtocol:
    """A protocol that still describes its code is reported as agreeing."""

    def test_everything_that_applied_agrees(self, monorepo: Path):
        protocol = _protocol(
            modules=[
                {
                    "module_id": "api",
                    "paths": ["services/api"],
                    "decisions_path": "docs/adr",
                    "tooling": {"test_command": "pytest"},
                    "validators": ["quality"],
                },
                {
                    "module_id": "web",
                    "paths": ["apps/web"],
                    "validators": ["quality"],
                },
            ],
            validator_tooling={"test_command": "uv run pytest -q"},
        )
        drift = compare_protocol(monorepo, _survey(monorepo), protocol)

        assert drift.divergences == []
        assert drift.has_divergences is False
        assert drift.uses_modules is True
        checks = {agreement.check for agreement in drift.agreements}
        assert checks == {
            CHECK_MODULE_PATHS,
            CHECK_MODULE_COVERAGE,
            CHECK_DECISION_RECORDS,
            CHECK_TEST_COMMANDS,
        }
        assert "Nothing the survey could compare has diverged" in drift.summary
        assert "2 declared module(s): api, web" in drift.shape

    def test_a_glob_declaration_resolves_to_its_literal_root(self, monorepo: Path):
        """ADR 041's own shape: `paths: ["services/api/**"]` claims a directory."""
        protocol = _protocol(modules=[
            {"module_id": "api", "paths": ["services/api/**"], "validators": ["quality"]},
        ])
        drift = compare_protocol(monorepo, _survey(monorepo), protocol)

        assert [f.subject for f in drift.divergences if f.check == CHECK_MODULE_PATHS] == []
        assert "services/api (governing api)" in next(
            a.evidence for a in drift.agreements if a.check == CHECK_MODULE_PATHS
        )
        # apps/web is a real boundary the single declared module does not reach
        assert [f.subject for f in drift.divergences
                if f.check == CHECK_MODULE_COVERAGE] == ["apps/web"]


class TestProtocolModulePathGone:
    def test_declared_path_that_no_longer_exists_is_reported(self, monorepo: Path):
        protocol = _protocol(modules=[
            {"module_id": "api", "paths": ["services/api"], "validators": ["quality"]},
            {"module_id": "worker", "paths": ["services/worker"], "validators": ["quality"]},
        ])
        drift = compare_protocol(monorepo, _survey(monorepo), protocol)

        paths = [f for f in drift.divergences if f.check == CHECK_MODULE_PATHS]
        assert len(paths) == 1
        assert paths[0].subject == "services/worker"
        assert paths[0].claim == "module worker governs 'services/worker'"
        assert "nothing exists" in paths[0].observation

    def test_a_path_named_by_several_modules_is_one_finding(self, monorepo: Path):
        protocol = _protocol(modules=[
            {"module_id": "api", "paths": ["services/gone"], "validators": ["quality"]},
            {"module_id": "web", "paths": ["services/gone"], "validators": ["quality"]},
        ])
        drift = compare_protocol(monorepo, _survey(monorepo), protocol)

        paths = [f for f in drift.divergences if f.check == CHECK_MODULE_PATHS]
        assert len(paths) == 1
        assert paths[0].claim == "modules api, web govern 'services/gone'"

    @pytest.mark.parametrize(
        "declared, fragment",
        [
            ("**/src", "no literal root"),
            ("/etc/services", "absolute path"),
            ("../side-by-side-repo", "escapes the repository root"),
        ],
        ids=["glob-only", "absolute", "escaping"],
    )
    def test_a_declaration_that_names_no_location_is_said_so(
        self, monorepo: Path, declared: str, fragment: str
    ):
        """No finding is invented from a declaration the filesystem cannot be asked about."""
        protocol = _protocol(modules=[
            {"module_id": "api", "paths": [declared], "validators": ["quality"]},
        ])
        drift = compare_protocol(monorepo, _survey(monorepo), protocol)

        assert [f for f in drift.divergences if f.check == CHECK_MODULE_PATHS] == []
        reasons = [n for n in drift.not_compared if n.check == CHECK_MODULE_PATHS]
        assert reasons and fragment in reasons[0].reason


class TestUngovernedModule:
    def test_a_boundary_the_protocol_does_not_reach_is_reported(self, monorepo: Path):
        _write(monorepo, "packages/legacy/package.json", '{"name": "legacy"}')
        protocol = _protocol(modules=[
            {"module_id": "api", "paths": ["services/api"], "validators": ["quality"]},
            {"module_id": "web", "paths": ["apps/web"], "validators": ["quality"]},
        ])
        drift = compare_protocol(monorepo, _survey(monorepo), protocol)

        coverage = [f for f in drift.divergences if f.check == CHECK_MODULE_COVERAGE]
        assert [f.subject for f in coverage] == ["packages/legacy"]
        # Reported as ungoverned, never as needing something.
        wording = coverage[0].claim + coverage[0].observation
        assert "validator" not in wording
        assert "should" not in wording
        assert "missing" not in wording

    def test_a_module_scope_reaching_inside_a_boundary_governs_it(self, monorepo: Path):
        _write(monorepo, "apps/web/package.json", '{"name": "web"}')
        protocol = _protocol(modules=[
            {"module_id": "api", "paths": ["services/api"], "validators": ["quality"]},
            {
                "module_id": "frontend",
                "paths": ["apps/web/src"],
                "validators": ["quality"],
            },
        ])
        drift = compare_protocol(monorepo, _survey(monorepo), protocol)

        assert [f for f in drift.divergences if f.check == CHECK_MODULE_COVERAGE] == []


class TestNoModulesIsTheCommonShape:
    """A protocol that declares no modules does not use modules; it is not drifting."""

    def test_no_discovered_module_is_reported_ungoverned(self, monorepo: Path):
        _write(monorepo, "packages/legacy/package.json", '{"name": "legacy"}')
        drift = compare_protocol(monorepo, _survey(monorepo), _protocol())

        assert drift.uses_modules is False
        assert drift.divergences == []
        coverage = [n for n in drift.not_compared if n.check == CHECK_MODULE_COVERAGE]
        assert len(coverage) == 1
        assert "declares no modules" in coverage[0].reason
        survey = _survey(monorepo)
        assert len(survey.modules) > 0, "the repository does carry boundaries"

    def test_shape_is_stated_so_the_reader_knows_what_was_weighed(self, monorepo: Path):
        drift = compare_protocol(monorepo, _survey(monorepo), _protocol())
        assert "no declared modules" in drift.shape

    def test_module_comparisons_are_all_listed_as_not_made(self, monorepo: Path):
        drift = compare_protocol(monorepo, _survey(monorepo), _protocol())
        checks = {n.check for n in drift.not_compared}
        assert {CHECK_MODULE_PATHS, CHECK_MODULE_COVERAGE, CHECK_DECISION_RECORDS} <= checks


class TestDecisionRecords:
    def test_a_moved_decision_directory_is_reported(self, monorepo: Path):
        protocol = _protocol(modules=[
            {
                "module_id": "api",
                "paths": ["services/api"],
                "decisions_path": "docs/adr-2019",
                "validators": ["quality"],
            },
        ])
        drift = compare_protocol(monorepo, _survey(monorepo), protocol)

        records = [f for f in drift.divergences if f.check == CHECK_DECISION_RECORDS]
        assert [f.subject for f in records] == ["docs/adr-2019"]
        assert "not a directory" in records[0].observation
        assert records[0].claim == "module api points its decision records at 'docs/adr-2019'"

    def test_an_emptied_decision_directory_is_reported(self, monorepo: Path):
        (monorepo / "docs/adr/001-shape.md").unlink()
        (monorepo / "docs/adr/notes.txt").write_text("nothing governed here\n")
        protocol = _protocol(modules=[
            {
                "module_id": "api",
                "paths": ["services/api"],
                "decisions_path": "docs/adr",
                "validators": ["quality"],
            },
        ])
        drift = compare_protocol(monorepo, _survey(monorepo), protocol)

        records = [f for f in drift.divergences if f.check == CHECK_DECISION_RECORDS]
        assert len(records) == 1
        assert "holds no markdown files" in records[0].observation

    def test_a_decision_directory_shared_by_two_modules_is_one_finding(self, monorepo: Path):
        protocol = _protocol(modules=[
            {
                "module_id": "api",
                "paths": ["services/api"],
                "decisions_path": "docs/gone",
                "validators": ["quality"],
            },
            {
                "module_id": "web",
                "paths": ["apps/web"],
                "decisions_path": "docs/gone",
                "validators": ["quality"],
            },
        ])
        drift = compare_protocol(monorepo, _survey(monorepo), protocol)

        records = [f for f in drift.divergences if f.check == CHECK_DECISION_RECORDS]
        assert len(records) == 1
        assert records[0].claim == "modules api, web point their decision records at 'docs/gone'"

    def test_a_decision_path_that_names_no_directory_is_not_guessed_at(
        self, monorepo: Path
    ):
        protocol = _protocol(modules=[
            {
                "module_id": "api",
                "paths": ["services/api"],
                "decisions_path": "**/adr",
                "validators": ["quality"],
            },
        ])
        drift = compare_protocol(monorepo, _survey(monorepo), protocol)

        assert [f for f in drift.divergences if f.check == CHECK_DECISION_RECORDS] == []
        reasons = [n.reason for n in drift.not_compared if n.check == CHECK_DECISION_RECORDS]
        assert any("no directory a filesystem can be asked about" in r for r in reasons)

    def test_no_modules_means_the_protocol_named_no_path_to_check(self, monorepo: Path):
        drift = compare_protocol(monorepo, _survey(monorepo), _protocol())
        records = [n for n in drift.not_compared if n.check == CHECK_DECISION_RECORDS]
        assert len(records) == 1
        assert "field on a module" in records[0].reason


class TestTestCommands:
    def test_a_command_with_no_runner_in_the_repository_is_reported(self, monorepo: Path):
        protocol = _protocol(validator_tooling={"test_command": "make test"})
        drift = compare_protocol(monorepo, _survey(monorepo), protocol)

        commands = [f for f in drift.divergences if f.check == CHECK_TEST_COMMANDS]
        assert [f.subject for f in commands] == ["make test"]
        assert "make" not in commands[0].observation  # phrased about the repository
        assert any("pytest" in ev for ev in commands[0].evidence)

    def test_a_module_command_resolves_from_its_own_scope_or_the_root(self, monorepo: Path):
        protocol = _protocol(modules=[
            {
                "module_id": "api",
                "paths": ["services/api"],
                "tooling": {"test_command": "pytest services/api"},
                "validators": ["quality"],
            },
        ])
        drift = compare_protocol(monorepo, _survey(monorepo), protocol)

        assert [f for f in drift.divergences if f.check == CHECK_TEST_COMMANDS] == []
        agreed = next(
            a for a in drift.agreements if a.check == CHECK_TEST_COMMANDS
        )
        assert "pytest services/api" in agreed.evidence[0]

    def test_a_slash_bearing_tooling_value_is_not_read_as_a_path(self, monorepo: Path):
        """`openai/gpt-4o-mini` is a model identifier; it exists nowhere on disk."""
        protocol = _protocol(validator_tooling={
            "test_command": "pytest",
            "model": "openai/gpt-4o-mini",
            "image": "ghcr.io/example/runner:latest",
            "query": "a/b/c",
        })
        drift = compare_protocol(monorepo, _survey(monorepo), protocol)

        assert [f for f in drift.divergences if f.check == CHECK_TEST_COMMANDS] == []
        assert not any("gpt-4o" in f.subject for f in drift.divergences)
        untouched = [n for n in drift.not_compared if n.check == CHECK_TOOLING_VALUES]
        assert len(untouched) == 1
        assert "'model'" in untouched[0].reason
        assert "'image'" in untouched[0].reason

    def test_an_unrecognised_command_is_not_guessed_at(self, monorepo: Path):
        protocol = _protocol(validator_tooling={
            "test_command": "./tooling/run-checks.sh --config=a/b/c.toml"
        })
        drift = compare_protocol(monorepo, _survey(monorepo), protocol)

        assert [f for f in drift.divergences if f.check == CHECK_TEST_COMMANDS] == []
        reasons = [n.reason for n in drift.not_compared if n.check == CHECK_TEST_COMMANDS]
        assert any("no runner survey recognises" in reason for reason in reasons)

    def test_the_placeholder_for_no_command_configured_asserts_nothing(self, monorepo: Path):
        protocol = _protocol(validator_tooling={
            "test_command": "REPLACE_ME",
        })
        drift = compare_protocol(monorepo, _survey(monorepo), protocol)

        assert [f for f in drift.divergences if f.check == CHECK_TEST_COMMANDS] == []
        reasons = [n.reason for n in drift.not_compared if n.check == CHECK_TEST_COMMANDS]
        assert any("placeholder" in reason for reason in reasons)

    def test_no_test_command_at_all_is_reported_as_nothing_to_compare(
        self, monorepo: Path
    ):
        drift = compare_protocol(monorepo, _survey(monorepo), _protocol())
        assert [f for f in drift.divergences if f.check == CHECK_TEST_COMMANDS] == []
        reasons = [n.reason for n in drift.not_compared if n.check == CHECK_TEST_COMMANDS]
        assert any("declares a tooling.test_command" in reason for reason in reasons)

    def test_a_command_assumed_by_a_validator_and_a_module_is_one_finding(
        self, monorepo: Path
    ):
        protocol = _protocol(
            validator_tooling={"test_command": "make test"},
            modules=[
                {
                    "module_id": "api",
                    "paths": ["services/api"],
                    "tooling": {"test_command": "make test"},
                    "validators": ["quality"],
                },
            ],
        )
        drift = compare_protocol(monorepo, _survey(monorepo), protocol)

        commands = [f for f in drift.divergences if f.check == CHECK_TEST_COMMANDS]
        assert len(commands) == 1
        assert "validator 'quality'" in commands[0].claim
        assert "module 'api'" in commands[0].claim


    def test_a_module_command_in_paths_that_are_gone_is_not_a_second_finding(
        self, monorepo: Path
    ):
        """The missing paths are the divergence; the runner is not re-tried elsewhere."""
        protocol = _protocol(modules=[
            {
                "module_id": "worker",
                "paths": ["services/worker"],
                "tooling": {"test_command": "pytest"},
                "validators": ["quality"],
            },
        ])
        drift = compare_protocol(monorepo, _survey(monorepo), protocol)

        assert [f.subject for f in drift.divergences
                if f.check == CHECK_MODULE_PATHS] == ["services/worker"]
        reasons = [n.reason for n in drift.not_compared if n.check == CHECK_TEST_COMMANDS]
        assert any("no scope to resolve in" in reason for reason in reasons)

    def test_a_validator_command_resolves_from_a_module_scope_it_delegates_to(
        self, monorepo: Path
    ):
        """No root manifest, but the repository does declare pytest inside a package."""
        protocol = _protocol(validator_tooling={"test_command": "pytest"})
        drift = compare_protocol(monorepo, _survey(monorepo), protocol)

        assert [f for f in drift.divergences if f.check == CHECK_TEST_COMMANDS] == []

    def test_a_no_modules_protocol_still_reports_an_unresolved_command(
        self, monorepo: Path
    ):
        """The one comparison this shape supports, on the shape that supports little."""
        protocol = _protocol(validator_tooling={"test_command": "cargo test"})
        drift = compare_protocol(monorepo, _survey(monorepo), protocol)

        commands = [f for f in drift.divergences if f.check == CHECK_TEST_COMMANDS]
        assert [f.subject for f in commands] == ["cargo test"]
        assert drift.uses_modules is False
        assert commands[0].claim == "validator 'quality' assumes the test command 'cargo test'"


    def test_a_nested_makefile_is_not_read_as_the_repository_root_one(
        self, monorepo: Path
    ):
        """A Makefile belongs to the repository root, as `_detect_test_command` holds.

        A `make test` assumed at protocol level is not confirmed by a Makefile
        buried in one package: that would report agreement where the repository
        declares the runner somewhere else entirely.
        """
        _write(monorepo, "apps/web/Makefile", "test:\n\tvitest run\n")
        protocol = _protocol(validator_tooling={"test_command": "make test"})
        drift = compare_protocol(monorepo, _survey(monorepo), protocol)

        commands = [f for f in drift.divergences if f.check == CHECK_TEST_COMMANDS]
        assert [f.subject for f in commands] == ["make test"]

    def test_a_module_command_is_confirmed_by_the_repository_root_makefile(
        self, monorepo: Path
    ):
        _write(monorepo, "Makefile", "test:\n\tvitest run\n")
        protocol = _protocol(modules=[
            {
                "module_id": "api",
                "paths": ["services/api"],
                "tooling": {"test_command": "make test"},
                "validators": ["quality"],
            },
        ])
        drift = compare_protocol(monorepo, _survey(monorepo), protocol)

        assert [f for f in drift.divergences if f.check == CHECK_TEST_COMMANDS] == []


class TestPlaceholderCommandsAgreeWithTheValidator:
    """The two readers of tooling.test_command must agree on "not configured".

    quality.py executes these values; drift.py declines to treat them as claims
    about the repository. A change to either list that the other does not follow
    would silently turn a placeholder into a divergence, so the sets are checked
    against each other rather than restated.
    """

    def test_every_value_the_quality_validator_treats_as_unconfigured_asserts_nothing(
        self,
    ):
        from snodo.validators.quality import _PLACEHOLDER_COMMANDS

        from snodo.survey.drift import _asserts_no_runner

        for value in sorted(_PLACEHOLDER_COMMANDS):
            assert _asserts_no_runner(value), value


class TestReportTakesNoSide:
    def test_a_divergence_states_both_sides_without_naming_a_fault(self, monorepo: Path):
        protocol = _protocol(modules=[
            {"module_id": "ghost", "paths": ["services/ghost"], "validators": ["quality"]},
        ])
        drift = compare_protocol(monorepo, _survey(monorepo), protocol)

        finding = next(f for f in drift.divergences if f.check == CHECK_MODULE_PATHS)
        assert finding.claim and finding.observation
        for word in ("outdated", "stale", "broken", "should", "needs", "must", "fault"):
            assert word not in finding.claim.lower()
            assert word not in finding.observation.lower()

    def test_divergences_and_agreements_both_appear_in_the_dict(self, monorepo: Path):
        protocol = _protocol(
            modules=[
                {"module_id": "api", "paths": ["services/api"], "validators": ["quality"]},
                {"module_id": "ghost", "paths": ["services/ghost"], "validators": ["quality"]},
            ],
            validator_tooling={"test_command": "pytest"},
        )
        payload = compare_protocol(monorepo, _survey(monorepo), protocol).to_dict()

        assert payload["has_divergences"] is True
        assert payload["uses_modules"] is True
        assert payload["divergences"] and payload["agreements"]
        assert payload["divergences"][0].keys() == {
            "check", "subject", "claim", "observation", "evidence",
        }
        assert payload["agreements"][0].keys() == {
            "check", "statement", "subject", "evidence",
        }
        assert payload["not_compared"][0].keys() == {"check", "reason"}


class TestUngovernedSurveyHasNoDrift:
    def test_a_survey_of_an_ungoverned_repository_carries_no_drift_report(
        self, monorepo: Path
    ):
        survey = _survey(monorepo)
        assert survey.drift is None
        assert "drift" not in survey.to_dict()

    def test_the_protocol_file_is_never_touched_by_the_comparison(self, monorepo: Path):
        _write(monorepo, ".snodo/protocol.yml", yaml.safe_dump(_protocol_body(
            modules=[
                {"module_id": "ghost", "paths": ["services/ghost"], "validators": ["quality"]},
            ]
        )))
        before = (monorepo / ".snodo/protocol.yml").read_bytes()
        survey = _survey(monorepo)
        compare_protocol(monorepo, survey, _protocol(
            modules=[
                {"module_id": "ghost", "paths": ["services/ghost"], "validators": ["quality"]},
            ]
        ))
        assert (monorepo / ".snodo/protocol.yml").read_bytes() == before

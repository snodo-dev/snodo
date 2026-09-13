"""CLI tests for surveying a governed repository.

FILE: tests/cli/test_survey_drift_cmd.py

The refusal is gone: a repository that already has a protocol is surveyed like
any other, and its report answers the governed question — does this protocol
still describe this code? These tests hold the two things that makes the
feature safe to run against a live project:

* the exit code classifies correctly — a run that found drift succeeded, and
  only a run that produced no report fails;
* nothing is written, in any case, in either direction.

Both protocol shapes are covered, because the one that declares no modules is
the common case: on that shape the module comparisons must be reported as not
made rather than as a page of findings, and the ungoverned report must stay
byte-for-byte what it was before drift existed.
"""

import json
import os
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from snodo.cli.commands import survey_cmd
from tests.survey.protocol_fixtures import (
    GOVERNED_FIXTURE,
    MODULES_AGREE,
    UNGOVERNED_FIXTURE,
    protocol_yaml,
    write_repository,
)

BASELINE_DIR = Path(__file__).resolve().parent.parent / "survey" / "baseline"
BASELINE_REPO_NAME = "baseline-repo"


def _init_git(root: Path) -> None:
    from git import Repo

    Repo.init(str(root))


@pytest.fixture
def run_survey(monkeypatch):
    """Run survey_command inside a directory, with no agent consulted."""
    monkeypatch.setattr(survey_cmd, "build_survey_judge", lambda root, mode: None)

    def _run(root: Path, *, as_json: bool = False, files=None, protocol_text=None):
        if files:
            write_repository(root, files)
        if protocol_text is not None:
            (root / ".snodo").mkdir(parents=True, exist_ok=True)
            (root / ".snodo" / "protocol.yml").write_text(protocol_text)
        if not (root / ".git").exists():
            _init_git(root)
        old = os.getcwd()
        os.chdir(root)
        try:
            rc = survey_cmd.survey_command(SimpleNamespace(json=as_json, agent=None))
        finally:
            os.chdir(old)
        return rc

    return _run


def _tree(root: Path) -> dict:
    """Every path and byte under *root*, so a write anywhere is visible."""
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class TestGovernedRepositoryIsSurveyed:
    def test_the_refusal_is_gone_from_both_streams(self, tmp_path, run_survey, capsys):
        rc = run_survey(
            tmp_path, files=GOVERNED_FIXTURE,
            protocol_text=protocol_yaml(modules=MODULES_AGREE),
        )
        captured = capsys.readouterr()
        assert rc == 0
        assert "already has a protocol" not in captured.out + captured.err
        assert "repositories without a declared protocol" not in captured.out + captured.err
        assert "Protocol Versus Code:" in captured.out

    def test_agreement_is_reported_as_plainly_as_divergence(
        self, tmp_path, run_survey, capsys
    ):
        rc = run_survey(
            tmp_path, files=GOVERNED_FIXTURE,
            protocol_text=protocol_yaml(modules=MODULES_AGREE),
        )
        out = capsys.readouterr().out
        assert rc == 0
        assert "Nothing the survey could compare has diverged" in out
        assert "Divergences (0):" in out
        assert "[protocol-module-paths]" in out
        assert "[decision-records]" in out

    def test_a_deleted_module_path_diverges_and_the_run_still_succeeds(
        self, tmp_path, run_survey, capsys
    ):
        rc = run_survey(
            tmp_path, files=GOVERNED_FIXTURE,
            protocol_text=protocol_yaml(modules=[
                {"module_id": "api", "paths": ["services/api"], "validators": ["quality"]},
                {"module_id": "worker", "paths": ["services/worker"], "validators": ["quality"]},
            ]),
        )
        out = capsys.readouterr().out
        assert rc == 0, "a run that found drift succeeded"
        assert "[protocol-module-paths] services/worker" in out
        assert "protocol claims:" in out
        assert "code shows:" in out

    def test_a_boundary_the_protocol_does_not_reach_is_reported_ungoverned(
        self, tmp_path, run_survey, capsys
    ):
        files = dict(GOVERNED_FIXTURE)
        files["packages/legacy/package.json"] = '{"name": "legacy"}'
        rc = run_survey(
            tmp_path, files=files,
            protocol_text=protocol_yaml(modules=MODULES_AGREE),
        )
        out = capsys.readouterr().out
        assert rc == 0
        assert "[module-coverage] packages/legacy" in out
        # Reported as ungoverned, never as a requirement.
        assert "needs a validator" not in out
        assert "should declare" not in out


class TestProtocolWithoutModulesIsTheCommonShape:
    def test_no_discovered_boundary_is_called_ungoverned(
        self, tmp_path, run_survey, capsys
    ):
        files = dict(GOVERNED_FIXTURE)
        files["packages/legacy/package.json"] = '{"name": "legacy"}'
        rc = run_survey(tmp_path, files=files, protocol_text=protocol_yaml())
        out = capsys.readouterr().out

        assert rc == 0
        assert "no declared modules" in out, "the report says which shape it is looking at"
        assert "Divergences (0):" in out
        assert "[module-coverage] packages/legacy" not in out
        assert "module coverage is a comparison a module-scoped protocol makes" in out

    def test_the_analysis_still_runs_on_a_governed_repository(
        self, tmp_path, run_survey, capsys
    ):
        """The ticket's real case: a governed repository could not be surveyed at all."""
        rc = run_survey(tmp_path, files=GOVERNED_FIXTURE, protocol_text=protocol_yaml())
        payload_dir = capsys.readouterr()
        assert rc == 0
        assert "Discovered Modules:" in payload_dir.out
        assert "services/api" in payload_dir.out


class TestDriftExitCodesAreAClassification:
    def test_drift_found_is_a_pass_and_a_failure_is_not(self, tmp_path, run_survey, capsys):
        rc_ok = run_survey(
            tmp_path, files=GOVERNED_FIXTURE,
            protocol_text=protocol_yaml(modules=MODULES_AGREE),
        )
        capsys.readouterr()
        rc_drift = run_survey(
            tmp_path / "other", files=GOVERNED_FIXTURE,
            protocol_text=protocol_yaml(modules=[
                {"module_id": "gone", "paths": ["services/gone"], "validators": ["quality"]},
            ]),
        )
        capsys.readouterr()
        rc_broken = run_survey(
            tmp_path / "broken", files=GOVERNED_FIXTURE, protocol_text="protocol: exists\n"
        )

        assert rc_ok == 0
        assert rc_drift == 0, "a run that found drift succeeded"
        assert rc_broken == 4, "a run that produced no report failed"
        assert rc_drift != rc_broken, "drift and failure are distinguishable"


class TestDriftJsonPayload:
    def test_shape_and_field_names_are_stable(self, tmp_path, run_survey, capsys):
        rc = run_survey(
            tmp_path, as_json=True, files=GOVERNED_FIXTURE,
            protocol_text=protocol_yaml(modules=MODULES_AGREE),
        )
        payload = json.loads(capsys.readouterr().out)
        assert rc == 0
        assert payload["schema"] == "snodo.survey.v1"
        assert payload["ok"] is True
        drift = payload["analysis"]["drift"]
        assert set(drift) == {
            "protocol_id", "uses_modules", "shape", "summary", "checks_made",
            "has_divergences", "agreements", "divergences", "not_compared",
        }
        assert drift["checks_made"] == len(drift["agreements"]) + len(drift["divergences"])
        assert drift["uses_modules"] is True
        assert drift["has_divergences"] is False
        assert {item["check"] for item in drift["agreements"]} == {
            "protocol-module-paths", "module-coverage", "decision-records",
            "test-commands",
        }
        assert drift["divergences"] == []

    def test_a_divergence_is_data_not_a_failure(self, tmp_path, run_survey, capsys):
        rc = run_survey(
            tmp_path, as_json=True, files=GOVERNED_FIXTURE,
            protocol_text=protocol_yaml(modules=[
                {"module_id": "api", "paths": ["services/gone"], "validators": ["quality"]},
            ]),
        )
        drift = json.loads(capsys.readouterr().out)["analysis"]["drift"]
        assert rc == 0
        assert drift["has_divergences"] is True
        assert "services/gone" in [f["subject"] for f in drift["divergences"]]
        assert set(drift["divergences"][0]) == {
            "check", "subject", "claim", "observation", "evidence",
        }

    def test_a_tooling_value_with_a_slash_is_not_reported_as_missing(
        self, tmp_path, run_survey, capsys
    ):
        rc = run_survey(
            tmp_path, as_json=True, files=GOVERNED_FIXTURE,
            protocol_text=protocol_yaml(
                modules=MODULES_AGREE,
                validator_tooling={
                    "test_command": "pytest",
                    "model": "openai/gpt-4o-mini",
                    "image": "ghcr.io/snodo/runner:latest",
                },
            ),
        )
        drift = json.loads(capsys.readouterr().out)["analysis"]["drift"]
        assert rc == 0
        assert drift["divergences"] == [], "a model identifier is not a missing path"
        assert "openai/gpt-4o-mini" not in json.dumps(drift["divergences"])
        untouched = [
            gap for gap in drift["not_compared"] if gap["check"] == "validator-tooling"
        ]
        assert len(untouched) == 1
        assert "'model'" in untouched[0]["reason"]
        assert "'image'" in untouched[0]["reason"]


    def test_a_snodo_directory_without_a_protocol_is_still_ungoverned(
        self, tmp_path, run_survey, capsys
    ):
        """Identity/state files do not make a repository governed."""
        (tmp_path / ".snodo").mkdir(parents=True)
        (tmp_path / ".snodo" / "project.json").write_text('{"id": "x", "scope": "local"}')
        rc = run_survey(tmp_path, as_json=True, files=dict(UNGOVERNED_FIXTURE))
        payload = json.loads(capsys.readouterr().out)
        assert rc == 0
        assert "drift" not in payload["analysis"]


class TestSurveyWritesNothing:
    @pytest.mark.parametrize(
        "modules",
        [MODULES_AGREE, None],
        ids=["with-modules", "without-modules"],
    )
    def test_a_governed_repository_is_untouched(self, tmp_path, run_survey, modules):
        files = dict(GOVERNED_FIXTURE)
        files["packages/legacy/package.json"] = '{"name": "legacy"}'
        run_survey(tmp_path, files=files, protocol_text=protocol_yaml(modules=modules))
        before = _tree(tmp_path)

        run_survey(tmp_path)  # second run, no setup: same repository
        assert _tree(tmp_path) == before
        assert not (tmp_path / ".snodo" / "drift.yml").exists()
        assert not (tmp_path / "protocol.proposal.yml").exists()
        assert not (tmp_path / ".snodo" / "proposals").exists()

    def test_an_ungoverned_repository_is_untouched(self, tmp_path, run_survey):
        run_survey(tmp_path, files=UNGOVERNED_FIXTURE)
        before = _tree(tmp_path)
        run_survey(tmp_path)
        assert _tree(tmp_path) == before
        assert not (tmp_path / ".snodo").exists()


class TestUngovernedReportIsUnchanged:
    """Today's ungoverned report is the baseline: identical bytes, no drift field."""

    @staticmethod
    def _normalise_human(text: str) -> str:
        text = re.sub(r"^Project ID: .*$", "Project ID: <ID> (<SCOPE>)", text,
                      flags=re.MULTILINE)
        return text.replace(
            f"Repository Analysis: {BASELINE_REPO_NAME}", "Repository Analysis: <NAME>"
        )

    def test_human_output_is_byte_identical_to_the_captured_baseline(
        self, tmp_path, run_survey, capsys
    ):
        root = tmp_path / BASELINE_REPO_NAME
        root.mkdir()
        rc = run_survey(root, files=UNGOVERNED_FIXTURE)
        out = self._normalise_human(capsys.readouterr().out)
        assert rc == 0
        assert out == (BASELINE_DIR / "ungoverned_human.txt").read_text()

    def test_json_payload_is_identical_to_the_captured_baseline(
        self, tmp_path, run_survey, capsys
    ):
        root = tmp_path / BASELINE_REPO_NAME
        root.mkdir()
        rc = run_survey(root, as_json=True, files=UNGOVERNED_FIXTURE)
        payload = json.loads(capsys.readouterr().out)
        payload["project_root"] = "<ROOT>"
        payload["project_id"] = "<ID>"
        expected = json.loads((BASELINE_DIR / "ungoverned_json.json").read_text())
        assert rc == expected["exit_code"]
        assert payload == expected["payload"]
        assert "drift" not in payload["analysis"]

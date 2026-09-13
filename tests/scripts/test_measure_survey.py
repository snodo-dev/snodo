"""Tests for scripts/measure_survey.py — the on-demand survey measurement."""

import importlib.util
import json
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "measure_survey.py"
_spec = importlib.util.spec_from_file_location("measure_survey", SCRIPT_PATH)
measure = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(measure)


def _make_repo(root: Path) -> None:
    app = root / "app"
    app.mkdir(parents=True)
    (app / "package.json").write_text(json.dumps({"name": "app", "scripts": {"build": "tsc"}}))
    (app / "index.ts").write_text("export const x = 1\n")


def _write_truth(path: Path, repo: Path, boundaries, languages) -> None:
    path.write_text(
        json.dumps(
            {
                "repositories": [
                    {
                        "name": "sample",
                        "path": str(repo),
                        "boundaries": list(boundaries),
                        "languages": list(languages),
                    }
                ]
            }
        )
    )


def test_measurement_scores_a_repository_against_its_truth(tmp_path):
    repo = tmp_path / "repo"
    _make_repo(repo)
    truth = tmp_path / "truth.json"
    _write_truth(truth, repo, {"app"}, {"typescript"})

    corpus = measure.run_measurement(truth)

    assert corpus.boundary_score.true_positives == 1
    assert corpus.boundary_score.false_positives == 0
    assert corpus.boundary_score.precision == 1.0
    assert corpus.boundary_score.recall == 1.0
    assert corpus.language_score.recall == 1.0


def test_main_prints_figures_and_check_fails_on_a_miss(tmp_path, capsys):
    repo = tmp_path / "repo"
    _make_repo(repo)
    truth = tmp_path / "truth.json"
    _write_truth(truth, repo, {"app", "missing"}, {"typescript"})

    assert measure.main([str(truth)]) == 0
    out = capsys.readouterr().out
    assert "Module boundaries: precision=" in out
    assert "Languages:" in out

    assert measure.main([str(truth), "--check"]) == 1
    out = capsys.readouterr().out
    assert "missing" in out


def test_relative_repository_paths_resolve_against_the_truth_file(tmp_path):
    repo = tmp_path / "repo"
    _make_repo(repo)
    truth = tmp_path / "truth.json"
    truth.write_text(
        json.dumps(
            {
                "repositories": [
                    {
                        "name": "sample",
                        "path": "repo",
                        "boundaries": ["app"],
                        "languages": ["typescript"],
                    }
                ]
            }
        )
    )

    corpus = measure.run_measurement(truth)

    assert corpus.boundary_score.recall == 1.0


def test_default_measurement_is_offline(tmp_path):
    repo = tmp_path / "repo"
    _make_repo(repo)
    truth = tmp_path / "truth.json"
    _write_truth(truth, repo, {"app"}, {"typescript"})

    corpus = measure.run_measurement(truth)

    assert corpus.measurements[0].survey.agent_consulted is False


def test_json_output_carries_the_figures(tmp_path, capsys):
    repo = tmp_path / "repo"
    _make_repo(repo)
    truth = tmp_path / "truth.json"
    _write_truth(truth, repo, {"app"}, {"typescript"})

    assert measure.main([str(truth), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["boundaries"]["recall"] == 1.0
    assert payload["languages"]["precision"] == 1.0

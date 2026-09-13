"""Offline judgement behaviour: the agent-judged pass without a model.

FILE: tests/survey/test_judge_behaviour.py

The judged pass is exercised through the judge callable the analyzer accepts,
so its behaviour is testable with a canned reply and no network. The cases
that matter most are the ones where a confident but unattributable or partial
answer must not be trusted:

- a verdict citing a file that does not exist is refused;
- an abstention is reported and leaves the deterministic answer standing;
- a reply cut off mid-object promotes and drops nothing.
"""

from __future__ import annotations

import json
from pathlib import Path

from snodo.survey.analyzer import analyze_repository


def _make_app_and_docs(root: Path) -> None:
    for name, source in (("app", "src/index.ts"), ("docs", "index.md")):
        module = root / name
        (module / Path(source).parent).mkdir(parents=True, exist_ok=True)
        (module / "package.json").write_text(json.dumps({"name": name}))
        (module / source).write_text("// content\n")


def _verdict(subject: str, verdict: str, reason: str, cited) -> dict:
    return {
        "subject": subject,
        "verdict": verdict,
        "reason": reason,
        "cited_files": list(cited),
    }


class TestJudgementsOffline:
    def test_verdict_citing_a_nonexistent_file_is_refused(self, tmp_path):
        _make_app_and_docs(tmp_path)

        def judge(dossier):
            return {
                "verdicts": [
                    _verdict(
                        "boundary-role:docs", "scaffolding",
                        "a doc site, not shipped", ["docs/imaginary.html"],
                    )
                ]
            }

        survey = analyze_repository(tmp_path, judge=judge)

        assert {m.module_id for m in survey.modules} == {"app", "docs"}
        assert all(r.verdict != "scaffolding" for r in survey.judgements)
        gap = next(u for u in survey.unmade_judgements if u.subject_path == "docs")
        assert "own source" in gap.reason

    def test_abstention_is_reported_and_deterministic_answer_stands(self, tmp_path):
        _make_app_and_docs(tmp_path)

        def judge(dossier):
            return {
                "verdicts": [
                    _verdict("boundary-role:docs", "abstain", "cannot tell", [])
                ]
            }

        survey = analyze_repository(tmp_path, judge=judge)

        assert {m.module_id for m in survey.modules} == {"app", "docs"}
        assert survey.judgements == []
        gap = next(u for u in survey.unmade_judgements if u.subject_path == "docs")
        assert "abstained" in gap.reason

    def test_truncated_reply_is_not_read_as_a_partial_verdict(self):
        from snodo.cli.commands.survey_cmd import _parse_verdicts

        truncated = (
            '{"judgements": ['
            '{"subject": "boundary-role:app", "verdict": "product", '
            '"reason": "the app", "cited_files": ["app/package.json"]}, '
            '{"subject": "boundary-role:docs", "verdict": "scaffolding", '
            '"reason": "docs"'
        )

        assert _parse_verdicts(truncated) is None

    def test_truncated_reply_promotes_and_drops_nothing(self, tmp_path):
        from snodo.cli.commands.survey_cmd import _parse_verdicts

        _make_app_and_docs(tmp_path)
        # A candidate the deterministic pass would never promote on its own.
        unknown = tmp_path / "unknown-app"
        (unknown / "src").mkdir(parents=True)
        for i in range(30):
            (unknown / "src" / f"feature{i}.ts").write_text("export const x = 1\n")

        truncated = (
            '{"judgements": ['
            '{"subject": "boundary-role:app", "verdict": "product", '
            '"reason": "the app", "cited_files": ["app/package.json"]}, '
            '{"subject": "boundary-role:docs", "verdict": "scaffolding", '
            '"reason": "docs"'
        )

        def judge(dossier):
            verdicts = _parse_verdicts(truncated)
            if verdicts is None:
                return {"reason": "the agent returned no parseable judgement verdicts"}
            return {"verdicts": verdicts}

        survey = analyze_repository(tmp_path, judge=judge)

        # The deterministic answer stands and the candidate is not promoted.
        assert {m.module_id for m in survey.modules} == {"app", "docs"}
        assert not any("unknown-app" in m.paths for m in survey.modules)
        kinds = {u.subject_path: u.kind for u in survey.unmade_judgements}
        assert kinds["unknown-app"] == "undeclared-boundary"
        assert all("no parseable" in u.reason for u in survey.unmade_judgements)

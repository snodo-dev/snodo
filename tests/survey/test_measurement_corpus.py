"""The fixture corpus that makes the survey's accuracy claim reproducible.

FILE: tests/survey/test_measurement_corpus.py

The survey's precision and recall were once measured by hand against eight
private repositories. This corpus keeps the structures that actually
discriminated between right and wrong answers, each as a small synthetic tree
carrying only what its case needs, plus a single declared ground truth. The
expected answer lives in :data:`GROUND_TRUTH`; nothing here asserts a boundary
or a language case by case. The figures are computed from that truth and
asserted as figures, so a regression shows up as precision or recall moving,
not as an example that happens to still pass.

The deterministic pass is scored directly. The agent-judged pass is scored
against a judge that replays the declared classifications over the same trees
— no network, no provider, no model.

Every fixture is a real failure before it was a fixture:
- a docs directory with its own package.json for a static site generator
- a tests directory with a playwright config
- a nested example package inside an application
- a vendored Pods tree, which must contribute neither language nor boundary
- a pubspec.yaml manifest the walk once did not see
- sibling manifests with no root workspace declaration
- npm's "no test specified" scaffold, which is not a test command
- .svelte, .astro, .swift and .dart source trees, each once invisible
- a manifest that does not parse, which must not crash the pass
- a static site of HTML/CSS with no manifest, once invisible as source
- a migrations directory whose .sql files are the storage model
- a repository whose only tooling is a Makefile and a workflows directory
- a coverage report of HTML/CSS, output the module is not written in
- a small manifest-less page directory with its own tests, once dropped
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Dict, List, Optional

import pytest

from snodo.survey.measure import (
    CorpusMeasurement,
    ExpectedRepository,
    measure_repository,
)


# ---------------------------------------------------------------------------
# Fixture builders — each is a small synthetic tree, only what the case needs
# ---------------------------------------------------------------------------

def _write(root: Path, rel: str, content: str = "") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _package(name: str, **extra: object) -> str:
    return json.dumps({"name": name, **extra})


def _build_multi_worker(root: Path) -> None:
    """Sibling manifests, no workspace declaration at the root."""
    for name in ("api", "worker", "web"):
        _write(root, f"{name}/package.json", _package(name, scripts={"build": "tsc"}))
        _write(root, f"{name}/src/index.ts", "export const served = true\n")


def _build_flutter_pubspec(root: Path) -> None:
    """A manifest that is pubspec.yaml, not a form the walk once parsed."""
    _write(root, "app/pubspec.yaml", "name: sample_app\nflutter:\n  sdk: flutter\n")
    _write(root, "app/lib/main.dart", "void main() {}\n")


def _build_vendored_pods(root: Path) -> None:
    """Pods/ is dependency tree: no language and no boundary from inside it."""
    _write(root, "package.json", _package("root-app"))
    _write(root, "index.js", "// own javascript\n")
    _write(root, "ios/Runner/AppDelegate.swift", "import UIKit\n")
    _write(root, "ios/Pods/VendoredLib/package.json", _package("vendored"))
    _write(root, "ios/Pods/VendoredLib/binding.cpp", "// vendored cpp\n")
    _write(root, "ios/Pods/VendoredLib/binding.h", "/* vendored c */\n")


def _build_unparsable_manifest(root: Path) -> None:
    """A manifest that does not parse must not crash or vanish."""
    _write(root, "svc/package.json", "{ this is not valid json ")
    _write(root, "svc/index.js", "// js\n")


def _build_npm_no_test_stub(root: Path) -> None:
    """npm init's scaffold test script announces its own absence."""
    _write(
        root,
        "package.json",
        _package(
            "stub",
            scripts={"test": 'echo "Error: no test specified" && exit 1'},
        ),
    )
    _write(root, "index.js", "// js\n")


def _build_extensions(root: Path) -> None:
    """Source trees shipping extensions the language table once lacked."""
    _write(root, "web/App.svelte", "<h1>hi</h1>\n")
    _write(root, "web/Page.astro", "---\n---\n")
    _write(root, "ios/Thing.swift", "import Foundation\n")
    _write(root, "mobile/main.dart", "void main() {}\n")


def _build_docs_static_site(root: Path) -> None:
    """A documentation directory carrying its own package.json."""
    _write(root, "app/package.json", _package("app", scripts={"build": "tsc"}))
    _write(root, "app/src/index.ts", "export const shipped = true\n")
    _write(root, "docs/package.json", _package("docs-site"))
    _write(root, "docs/index.md", "# Documentation\n")
    _write(root, "docs/.vitepress/config.ts", "export default {}\n")


def _build_tests_playwright(root: Path) -> None:
    """A test directory with a playwright config."""
    _write(root, "app/package.json", _package("app", scripts={"build": "tsc"}))
    _write(root, "app/src/index.ts", "export const shipped = true\n")
    _write(root, "tests/package.json", _package("e2e"))
    _write(root, "tests/playwright.config.ts", "export default {}\n")
    _write(root, "tests/example.spec.ts", "test('x', () => {})\n")


def _build_nested_example(root: Path) -> None:
    """A nested example package inside an application: real manifest, scaffolding."""
    _write(root, "app/package.json", _package("app", scripts={"build": "tsc"}))
    _write(root, "app/index.js", "// the application\n")
    _write(root, "app/example/package.json", _package("example"))
    _write(root, "app/example/index.js", "// the example\n")


def _build_static_site(root: Path) -> None:
    """A deployed static site with no manifest: markup, styles, its own tests.

    HTML and CSS are source, so this directory must reach the judgement as a
    candidate. It is never promoted to a boundary on the file count alone.
    """
    for i in range(30):
        _write(root, f"web/page{i:02d}.html", f"<h1>Page {i}</h1>\n")
    _write(root, "web/assets/site.css", "h1 { color: black; }\n")
    _write(root, "web/requirements-test.txt", "pytest\n")
    _write(root, "web/tests/test_pages.py", "def test_pages():\n    assert True\n")


def _build_coverage_report(root: Path) -> None:
    """A module whose coverage/ report holds HTML and CSS it is not written in.

    The report is output a tool rendered from the module's own source, so it
    must contribute neither language nor boundary; the module is TypeScript.
    """
    _write(root, "app/package.json", _package("app"))
    _write(root, "app/src/index.ts", "export const served = true\n")
    for i in range(7):
        _write(root, f"app/coverage/p{i}.html", "<!doctype html>\n")
    _write(root, "app/coverage/base.css", "body { margin: 0; }\n")


def _build_handwritten_site(root: Path) -> None:
    """A small manifest-less directory of hand-written pages and its own tests.

    Three pages are not source-dense, but the directory carries its own test
    suite, so it must reach judgement as a candidate without being promoted
    to a boundary on arithmetic alone.
    """
    for i in range(3):
        _write(root, f"web/page{i}.html", f"<h1>Page {i}</h1>\n")
    _write(root, "web/assets/site.css", "h1 { color: black; }\n")
    _write(root, "web/requirements-test.txt", "pytest\n")
    _write(root, "web/tests/test_pages.py", "def test_pages():\n    assert True\n")


def _build_sql_migrations(root: Path) -> None:
    """A module whose storage model lives in .sql migration files."""
    _write(root, "api/pyproject.toml", "[project]\nname = \"api\"\n")
    for i in range(10):
        _write(root, f"api/migrations/{i:04d}_step.sql", "SELECT 1;\n")


def _build_makefile_tooling(root: Path) -> None:
    """Only repository-level tooling: a Makefile and a CI workflows directory."""
    _write(root, "Makefile", "build:\n\tnpm run build\n")
    _write(root, ".github/workflows/ci.yml", "name: ci\non: [push]\n")


FIXTURES: Dict[str, Callable[[Path], None]] = {
    "multi_worker": _build_multi_worker,
    "flutter_pubspec": _build_flutter_pubspec,
    "vendored_pods": _build_vendored_pods,
    "unparsable_manifest": _build_unparsable_manifest,
    "npm_no_test_stub": _build_npm_no_test_stub,
    "extensions": _build_extensions,
    "docs_static_site": _build_docs_static_site,
    "tests_playwright": _build_tests_playwright,
    "nested_example": _build_nested_example,
    "static_site": _build_static_site,
    "handwritten_site": _build_handwritten_site,
    "coverage_report": _build_coverage_report,
    "sql_migrations": _build_sql_migrations,
    "makefile_tooling": _build_makefile_tooling,
}


# ---------------------------------------------------------------------------
# The declared ground truth — the one place the expected answers live
# ---------------------------------------------------------------------------

GROUND_TRUTH: List[ExpectedRepository] = [
    ExpectedRepository(
        name="multi_worker",
        boundaries=frozenset({"api", "worker", "web"}),
        languages=frozenset({"typescript"}),
    ),
    ExpectedRepository(
        name="flutter_pubspec",
        boundaries=frozenset({"app"}),
        languages=frozenset({"dart"}),
    ),
    ExpectedRepository(
        name="vendored_pods",
        boundaries=frozenset(),
        languages=frozenset({"javascript", "swift"}),
    ),
    ExpectedRepository(
        name="unparsable_manifest",
        boundaries=frozenset({"svc"}),
        languages=frozenset({"javascript"}),
    ),
    ExpectedRepository(
        name="npm_no_test_stub",
        boundaries=frozenset(),
        languages=frozenset({"javascript"}),
        test_command=None,
    ),
    ExpectedRepository(
        name="extensions",
        boundaries=frozenset(),
        languages=frozenset({"svelte", "astro", "swift", "dart"}),
    ),
    ExpectedRepository(
        name="docs_static_site",
        boundaries=frozenset({"app"}),
        languages=frozenset({"typescript"}),
        scaffolding=frozenset({"docs"}),
    ),
    ExpectedRepository(
        name="tests_playwright",
        boundaries=frozenset({"app"}),
        languages=frozenset({"typescript"}),
        scaffolding=frozenset({"tests"}),
    ),
    ExpectedRepository(
        name="nested_example",
        boundaries=frozenset({"app"}),
        languages=frozenset({"javascript"}),
        scaffolding=frozenset({"app/example"}),
    ),
    ExpectedRepository(
        name="static_site",
        boundaries=frozenset({"web"}),
        languages=frozenset({"html", "css", "python"}),
        undeclared_modules=frozenset({"web"}),
    ),
    ExpectedRepository(
        name="handwritten_site",
        boundaries=frozenset({"web"}),
        languages=frozenset({"html", "css", "python"}),
        undeclared_modules=frozenset({"web"}),
    ),
    ExpectedRepository(
        name="coverage_report",
        boundaries=frozenset({"app"}),
        languages=frozenset({"typescript"}),
    ),
    ExpectedRepository(
        name="sql_migrations",
        boundaries=frozenset({"api"}),
        languages=frozenset({"sql"}),
    ),
    ExpectedRepository(
        name="makefile_tooling",
        boundaries=frozenset(),
        languages=frozenset(),
    ),
]


# ---------------------------------------------------------------------------
# The offline judge: replay the declared classifications, cite real files
# ---------------------------------------------------------------------------

def _cite_file(subject: Dict) -> Optional[str]:
    """A real file inside the subject that the verdict can rest on."""
    manifests = subject["evidence"].get("manifests") or {}
    for key in sorted(manifests):
        return key
    listing = subject["evidence"].get("listing") or []
    for entry in listing:
        if not entry.endswith("/"):
            return f"{subject['path']}/{entry}"
    return None


def _replay_judge(expected: ExpectedRepository):
    """A judge that answers from the ground truth, with no model involved."""

    def judge(dossier):
        verdicts = []
        for subject in dossier["subjects"]:
            path = subject["path"]
            if subject["kind"] == "boundary-role":
                verdict = "scaffolding" if path in expected.scaffolding else "product"
            else:
                verdict = "module" if path in expected.undeclared_modules else "not-module"
            cite = _cite_file(subject)
            if cite is None:
                verdicts.append({
                    "subject": subject["id"],
                    "verdict": "abstain",
                    "reason": "no file inside the subject to cite",
                })
                continue
            verdicts.append({
                "subject": subject["id"],
                "verdict": verdict,
                "reason": "replayed from the declared ground truth",
                "cited_files": [cite],
            })
        return {"verdicts": verdicts}

    return judge


# ---------------------------------------------------------------------------
# Corpus harness
# ---------------------------------------------------------------------------

def _build_corpus(root: Path) -> Dict[str, Path]:
    roots: Dict[str, Path] = {}
    for expected in GROUND_TRUTH:
        repo = root / expected.name
        repo.mkdir(parents=True, exist_ok=True)
        FIXTURES[expected.name](repo)
        roots[expected.name] = repo
    return roots


def _measure(root: Path, judged: bool) -> CorpusMeasurement:
    roots = _build_corpus(root)
    measurements = []
    for expected in GROUND_TRUTH:
        judge = _replay_judge(expected) if judged else None
        mode = "force" if judged else "off"
        measurements.append(
            measure_repository(expected, roots[expected.name], judge=judge, judge_mode=mode)
        )
    return CorpusMeasurement(measurements)


class TestFixtureCorpus:
    """The corpus reproduces the measurement's shape, figure by figure."""

    def test_every_fixture_meets_its_expected_answer(self, tmp_path):
        _measure(tmp_path, judged=True).assert_met()

    def test_the_corpus_is_the_declared_ground_truth(self):
        assert {e.name for e in GROUND_TRUTH} == set(FIXTURES), (
            "every fixture must have a declared expected answer, and vice versa"
        )

    def test_judged_pass_reproduces_the_claim(self, tmp_path):
        corpus = _measure(tmp_path, judged=True)

        assert corpus.boundary_score.true_positives == 12
        assert corpus.boundary_score.false_positives == 0
        assert corpus.boundary_score.false_negatives == 0
        assert corpus.boundary_score.precision == 1.0
        assert corpus.boundary_score.recall == 1.0

        # Language recall and precision are both exact now: the pubspec.yaml
        # manifest is identified as a manifest, so it is no longer counted as
        # a `yaml` source file of the language it declares, and the generated
        # coverage report's HTML and CSS are output, not the module's language.
        assert corpus.language_score.true_positives == 21
        assert corpus.language_score.false_positives == 0
        assert corpus.language_score.false_negatives == 0
        assert corpus.language_score.precision == 1.0
        assert corpus.language_score.recall == 1.0
        assert corpus.mismatches() == []

    def test_deterministic_pass_shows_the_scaffolding_false_positives(self, tmp_path):
        deterministic = _measure(tmp_path, judged=False)
        judged = _measure(tmp_path, judged=True)

        # The deterministic pass recalls every manifest-backed boundary; the
        # manifest of a scaffolding work is a proposal that judgement then
        # withdraws. The manifest-less sites are only ever candidates — the
        # source-dense one and the small one with its own tests — so it takes
        # judgement to recall them at all.
        assert deterministic.boundary_score.true_positives == 10
        assert deterministic.boundary_score.false_positives == 3
        assert deterministic.boundary_score.false_negatives == 2
        assert deterministic.boundary_score.recall == pytest.approx(10 / 12)
        assert deterministic.boundary_score.precision == pytest.approx(10 / 13)
        assert judged.boundary_score.precision > deterministic.boundary_score.precision

    def test_a_broken_expectation_fails_for_the_right_reason(self, tmp_path):
        """A wrong entry in the ground truth must fail, naming the subject."""
        broken = [
            expected
            if expected.name != "docs_static_site"
            else ExpectedRepository(
                name=expected.name,
                # Wrong on purpose: docs/ is scaffolding, not a product module.
                boundaries=frozenset({"app", "docs"}),
                languages=expected.languages,
                scaffolding=expected.scaffolding,
            )
            for expected in GROUND_TRUTH
        ]
        roots = _build_corpus(tmp_path)
        measurements = [
            measure_repository(
                expected, roots[expected.name], judge=_replay_judge(expected), judge_mode="force"
            )
            for expected in broken
        ]

        with pytest.raises(AssertionError) as excinfo:
            CorpusMeasurement(measurements).assert_met()

        message = str(excinfo.value)
        assert "docs_static_site" in message
        assert "missing boundaries" in message
        assert "docs" in message

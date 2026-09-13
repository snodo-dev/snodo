"""Tests for repository survey analyzer.

FILE: tests/survey/test_analyzer.py

Tests that the analyzer correctly:
1. Discovers module boundaries from workspace markers
2. Detects languages and tooling per module
3. Identifies test commands from marker files
4. Locates decision records
5. Never infers requirements from absence of practices
6. Proposes undeclared boundaries arithmetically and leaves their status to
   judgement
7. Accepts agent judgements only when they cite gathered evidence, and
   reports every judgement that was not made
"""

import json
import tempfile
from pathlib import Path

from snodo.survey.analyzer import (
    _DOSSIER_MAX_MANIFEST_CHARS,
    analyze_repository,
)

_DOSSIER_TRUNC_NOTE_LIMIT = _DOSSIER_MAX_MANIFEST_CHARS + 40


class TestSurveyAnalyzer:
    """Tests for repository survey analysis."""

    def test_single_package_repository_with_tests(self):
        """Test analysis of single-package repository with test marker."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)

            # Create a single-package Python project
            (project_root / "src").mkdir()
            (project_root / "src" / "main.py").write_text("print('hello')")
            (project_root / "pyproject.toml").write_text(
                "[project]\nname = 'test'\n\n[tool.pytest.ini_options]\ntestpaths = ['src']"
            )

            # Analyze
            survey = analyze_repository(project_root)

            # Single package detected
            assert len(survey.modules) == 0  # No workspaces or nested manifests
            assert "python" in survey.languages
            assert survey.test_command == "pytest"
            assert survey.test_marker_file == "pyproject.toml"

    def test_no_decisions_directory(self):
        """Test that absence of decisions directory is not inferred as a requirement."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)

            # Create a simple project with no decisions
            (project_root / "src").mkdir()
            (project_root / "src" / "main.py").write_text("print('hello')")

            # Analyze
            survey = analyze_repository(project_root)

            # Should correctly report no decisions found, not require them
            assert survey.decision_paths == []

    def test_monorepo_with_npm_workspaces(self):
        """Test detection of npm monorepo workspaces."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)

            # Create npm monorepo
            package_json = {
                "name": "monorepo",
                "workspaces": ["packages/api", "packages/web"],
            }
            import json
            (project_root / "package.json").write_text(json.dumps(package_json))

            # Create workspace packages
            api_dir = project_root / "packages" / "api"
            api_dir.mkdir(parents=True)
            (api_dir / "package.json").write_text("{}")

            web_dir = project_root / "packages" / "web"
            web_dir.mkdir(parents=True)
            (web_dir / "package.json").write_text("{}")

            # Analyze
            survey = analyze_repository(project_root)

            # Should detect modules
            assert len(survey.modules) == 2
            module_ids = {m.module_id for m in survey.modules}
            assert "api" in module_ids or "web" in module_ids

    def test_no_test_command_no_inference(self):
        """Test that missing test command is reported but not inferred as error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)

            # Create a project with no test marker files
            (project_root / "src").mkdir()
            (project_root / "src" / "main.py").write_text("print('hello')")

            # Analyze
            survey = analyze_repository(project_root)

            # Should report no test command found
            assert survey.test_command is None
            # But should NOT say this violates any requirement
            assert not any("require" in f.message.lower() for f in survey.findings)

    def test_decision_records_detection(self):
        """Test detection of decision record directories."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)

            # Create decisions directory with markdown files
            decisions_dir = project_root / "docs" / "decisions"
            decisions_dir.mkdir(parents=True)
            (decisions_dir / "001-init.md").write_text("# Initial Decision")
            (decisions_dir / "002-architecture.md").write_text("# Architecture")

            # Analyze
            survey = analyze_repository(project_root)

            # Should detect the decisions directory
            assert "docs/decisions" in survey.decision_paths

    def test_no_protocol_file_requirement(self):
        """Test that missing .snodo/protocol.yml is not reported as a finding."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)

            # Create a project with no protocol
            (project_root / "src").mkdir()
            (project_root / "src" / "main.py").write_text("print('hello')")

            # Analyze
            survey = analyze_repository(project_root)

            # Should not complain about missing protocol
            # (that's init's job, not survey's)
            assert not any(".snodo" in f.message for f in survey.findings)

    def test_language_detection(self):
        """Test language detection across project."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)

            # Create files in multiple languages
            (project_root / "api").mkdir()
            (project_root / "api" / "main.py").write_text("# python")
            (project_root / "web").mkdir()
            (project_root / "web" / "app.ts").write_text("// typescript")

            # Analyze
            survey = analyze_repository(project_root)

            # Should detect both languages
            assert "python" in survey.languages
            assert "typescript" in survey.languages

    def test_cargo_workspace_detection(self):
        """Test detection of Rust Cargo workspaces."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)

            # Create Cargo.toml with workspace
            cargo_toml = """
[workspace]
members = ["crate_a", "crate_b"]
"""
            (project_root / "Cargo.toml").write_text(cargo_toml)

            # Create workspace members
            (project_root / "crate_a").mkdir()
            (project_root / "crate_b").mkdir()

            # Analyze
            survey = analyze_repository(project_root)

            # Should detect modules
            assert len(survey.modules) > 0


class TestGrownNotScaffoldedRepository:
    """Regression tests for a repository shaped like the seven-service survey miss.

    Sibling service directories each hold their own manifest with no root
    workspace declaration, the Makefile has no test target, node_modules is
    populated, and tooling configuration lives in a dotted directory.
    """

    @staticmethod
    def _make_repo(root: Path) -> None:
        import json

        # Makefile with operational targets but no test target
        (root / "Makefile").write_text(
            "help:\n\t@echo help\n"
            "install-api: api.droptrack.io\n\tnpm install\n"
            "install:\n\tnpm install\n"
            "deploy-prod:\n\tnpm run deploy\n"
            "dev:\n\tnpm run dev\n"
            "stop:\n\tdocker compose down\n"
        )

        # Service directories, each declaring itself by manifest
        services = [
            "api.droptrack.io",
            "app.droptrack.io",
            "core.droptrack.io",
            "play.droptrack.io/droptrack-play",
        ]
        for service in services:
            service_dir = root / service
            (service_dir / "src").mkdir(parents=True)
            (service_dir / "package.json").write_text(
                json.dumps({"name": service, "scripts": {"dev": "vite", "build": "vite build"}})
            )
            (service_dir / "src" / "index.ts").write_text("export const served = true")

        # Tooling configuration in a dotted directory — not a product module
        (root / ".opencode").mkdir()
        (root / ".opencode" / "package.json").write_text('{"name": "tooling"}')
        (root / ".opencode" / "theme.php").write_text("<?php // vendored tooling\n")

        # A populated dependency tree holding languages the repository lacks
        vendored = root / "node_modules" / "left-pad"
        vendored.mkdir(parents=True)
        (vendored / "index.js").write_text("// vendored javascript\n")
        (vendored / "legacy.php").write_text("<?php // vendored php\n")
        (vendored / "bindings.c").write_text("/* vendored c */\n")
        (vendored / "glue.cpp").write_text("// vendored cpp\n")
        (vendored / "build.sh").write_text("#!/bin/sh\nexit 0\n")

    def test_modules_found_without_workspace_declaration(self, tmp_path):
        """Nested manifests are boundaries even when nothing upstream announces them."""
        self._make_repo(tmp_path)

        survey = analyze_repository(tmp_path)

        paths = {p for module in survey.modules for p in module.paths}
        assert "api.droptrack.io" in paths
        assert "app.droptrack.io" in paths
        assert "core.droptrack.io" in paths
        assert "play.droptrack.io/droptrack-play" in paths
        assert len(survey.modules) == 4

    def test_dotted_directory_is_tooling_not_module(self, tmp_path):
        """A manifest inside a dotted directory is configuration, not a service."""
        self._make_repo(tmp_path)

        survey = analyze_repository(tmp_path)

        assert not any(p.startswith(".") for module in survey.modules for p in module.paths)

    def test_no_test_command_claimed_from_makefile_without_test_target(self, tmp_path):
        """A Makefile without a test target must not certify `make test`."""
        self._make_repo(tmp_path)

        survey = analyze_repository(tmp_path)

        assert survey.test_command is None
        assert survey.test_marker_file is None
        for module in survey.modules:
            assert module.test_command is None
        serialized = str(survey.to_dict())
        assert "make test" not in serialized
        assert "npm test" not in serialized

    def test_languages_come_from_own_source_not_dependencies(self, tmp_path):
        """No language is reported that has no file outside the dependency tree."""
        self._make_repo(tmp_path)

        survey = analyze_repository(tmp_path)

        reported = set(survey.languages)
        for module in survey.modules:
            reported |= set(module.languages)
        assert reported == {"typescript"}
        for vendored_language in ("javascript", "php", "c", "cpp", "shell"):
            assert vendored_language not in reported

    def test_absent_test_command_is_not_framed_as_violation(self, tmp_path):
        """A deferred quality gate is reported plainly, not as a requirement."""
        self._make_repo(tmp_path)

        survey = analyze_repository(tmp_path)

        for finding in survey.findings:
            lowered = finding.message.lower()
            assert "violat" not in lowered
            assert "missing" not in lowered
            assert "require" not in lowered

    def test_analyzer_writes_nothing(self, tmp_path):
        """Survey reads the repository; it never writes into it."""
        self._make_repo(tmp_path)

        before = {p.relative_to(tmp_path) for p in tmp_path.rglob("*")}
        analyze_repository(tmp_path)
        after = {p.relative_to(tmp_path) for p in tmp_path.rglob("*")}

        assert before == after
        assert not (tmp_path / ".snodo").exists()


class TestTestCommandConfirmation:
    """Marker contents, not marker presence, decide what is claimed."""

    def test_makefile_with_test_target_is_confirmed(self, tmp_path):
        (tmp_path / "Makefile").write_text(
            "help:\n\t@echo help\n\ntest:\n\tpytest -q\n"
        )
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("print('hi')")

        survey = analyze_repository(tmp_path)

        assert survey.test_command == "make test"
        assert survey.test_marker_file == "Makefile"

    def test_package_json_without_test_script_is_not_claimed(self, tmp_path):
        (tmp_path / "package.json").write_text(
            '{"name": "solo", "scripts": {"build": "vite build"}}'
        )
        (tmp_path / "index.js").write_text("// js")

        survey = analyze_repository(tmp_path)

        assert survey.test_command is None

    def test_package_json_with_test_script_is_confirmed(self, tmp_path):
        (tmp_path / "package.json").write_text(
            '{"name": "solo", "scripts": {"test": "vitest run"}}'
        )
        (tmp_path / "index.js").write_text("// js")

        survey = analyze_repository(tmp_path)

        assert survey.test_command == "npm test"
        assert survey.test_marker_file == "package.json"

    def test_package_json_npm_default_stub_is_not_claimed(self, tmp_path):
        """npm init's scaffold test script announces its own absence; it always exits 1."""
        (tmp_path / "package.json").write_text(
            json.dumps(
                {
                    "name": "core.droptrack.io",
                    "version": "1.0.0",
                    "scripts": {"test": 'echo "Error: no test specified" && exit 1'},
                }
            )
        )
        (tmp_path / "index.js").write_text("// js")

        survey = analyze_repository(tmp_path)

        assert survey.test_command is None
        assert survey.test_marker_file is None
        serialized = str(survey.to_dict())
        assert "npm test" not in serialized
        for finding in survey.findings:
            lowered = finding.message.lower()
            assert "violat" not in lowered
            assert "missing" not in lowered
            assert "require" not in lowered

    def test_makefile_without_test_target_still_not_claimed(self, tmp_path):
        """The Makefile confirmer is untouched: no test target, no command."""
        (tmp_path / "Makefile").write_text(
            "help:\n\t@echo help\n\ndeploy:\n\tnpm run deploy\n"
        )
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("print('hi')")

        survey = analyze_repository(tmp_path)

        assert survey.test_command is None
        assert survey.test_marker_file is None

    def test_pyproject_without_pytest_is_not_claimed(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'bare'")
        (tmp_path / "main.py").write_text("print('hi')")

        survey = analyze_repository(tmp_path)

        assert survey.test_command is None

    def test_pyproject_with_pytest_dependency_is_confirmed(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text(
            "[project]\nname = 'pkg'\ndependencies = ['requests>=2.0']\n\n"
            "[dependency-groups]\ndev = ['pytest>=8', 'ruff']\n"
        )
        (tmp_path / "main.py").write_text("print('hi')")

        survey = analyze_repository(tmp_path)

        assert survey.test_command == "pytest"


def _verdict(subject_id, verdict, reason="because the evidence says so", cited=("package.json",)):
    return {
        "subject": subject_id,
        "verdict": verdict,
        "reason": reason,
        "cited_files": list(cited),
    }


def _recording_judge(verdicts):
    """A judge that returns canned verdicts and records the dossiers it saw."""
    calls = []

    def judge(dossier):
        calls.append(dossier)
        return {"verdicts": verdicts}

    judge.calls = calls
    return judge


class TestExtensionArithmetic:
    """Languages that exist on disk must appear in the survey."""

    def test_svelte_and_astro_are_detected(self, tmp_path):
        (tmp_path / "package.json").write_text('{"name": "site"}')
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "App.svelte").write_text("<h1>hi</h1>")
        (tmp_path / "src" / "Page.astro").write_text("---\n---")

        survey = analyze_repository(tmp_path)

        assert "svelte" in survey.languages
        assert "astro" in survey.languages

    def test_swift_is_detected(self, tmp_path):
        (tmp_path / "main.swift").write_text("print(\"hi\")")

        survey = analyze_repository(tmp_path)

        assert "swift" in survey.languages

    def test_pods_tree_contributes_no_language(self, tmp_path):
        """A vendored iOS dependency tree is pruned like node_modules is."""
        (tmp_path / "trival-app").mkdir()
        own = tmp_path / "trival-app" / "ios" / "Runner"
        own.mkdir(parents=True)
        (own / "AppDelegate.swift").write_text("import UIKit")
        pods = tmp_path / "trival-app" / "ios" / "Pods"
        pods.mkdir()
        (pods / "Something.h").write_text("/* vendored */")
        (pods / "Something.cpp").write_text("// vendored")

        survey = analyze_repository(tmp_path)

        reported = set(survey.languages)
        for module in survey.modules:
            reported |= set(module.languages)
        assert "swift" in reported
        assert "cpp" not in reported
        assert "c" not in reported


class TestManifestsTheWalkKnows:
    """A manifest the stack moved on to is still arithmetic once recognised."""

    def test_flutter_app_is_found_by_its_pubspec(self, tmp_path):
        app = tmp_path / "my-app"
        (app / "lib").mkdir(parents=True)
        (app / "pubspec.yaml").write_text(
            "name: my_app\ndescription: A Flutter application\n"
            "flutter:\n  sdk: flutter\n"
        )
        (app / "lib" / "main.dart").write_text("void main() {}")

        survey = analyze_repository(tmp_path)

        assert [m.paths for m in survey.modules] == [["my-app"]]
        assert "dart" in survey.modules[0].languages
        # a source-dense app tree with a manifest we now parse is found
        # arithmetically; it is never an undeclared-boundary candidate
        assert not [
            u for u in survey.unmade_judgements
            if u.kind == "undeclared-boundary" and u.subject_path == "my-app"
        ]


class TestManifestIsNotSource:
    """A file identified as a manifest is not also a language of the module."""

    def test_pubspec_manifest_is_not_counted_as_yaml(self, tmp_path):
        (tmp_path / "pubspec.yaml").write_text("name: x\n")
        (tmp_path / "main.dart").write_text("void main() {}\n")

        survey = analyze_repository(tmp_path)

        assert "dart" in survey.languages
        assert "yaml" not in survey.languages


class TestMarkupStylesAndSchemaAreSource:
    """Markup, stylesheets and schema are languages when the source is there."""

    def test_html_css_and_sql_are_detected(self, tmp_path):
        (tmp_path / "index.html").write_text("<h1>hi</h1>\n")
        (tmp_path / "site.css").write_text("h1 { color: black; }\n")
        (tmp_path / "migrations").mkdir()
        (tmp_path / "migrations" / "0001_init.sql").write_text("SELECT 1;\n")

        survey = analyze_repository(tmp_path)

        assert {"html", "css", "sql"} <= set(survey.languages)


class TestRepositoryTooling:
    """Repository-level facts are reported as tooling, not as source or modules."""

    def test_makefile_and_workflows_directory_are_reported(self, tmp_path):
        (tmp_path / "Makefile").write_text("build:\n\tnpm run build\n")
        workflows = tmp_path / ".github" / "workflows"
        workflows.mkdir(parents=True)
        (workflows / "ci.yml").write_text("name: ci\non: [push]\n")
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("print('hi')\n")

        survey = analyze_repository(tmp_path)

        assert "Makefile" in survey.repository_tooling
        assert ".github/workflows" in survey.repository_tooling
        # tooling is a repository-level fact, never a language or a module
        assert ".github/workflows" not in survey.languages
        assert not any(m.paths for m in survey.modules)

    def test_root_lockfile_is_reported_as_tooling(self, tmp_path):
        (tmp_path / "package-lock.json").write_text("{}\n")
        (tmp_path / "index.js").write_text("// js\n")

        survey = analyze_repository(tmp_path)

        assert "package-lock.json" in survey.repository_tooling

    def test_no_tooling_reports_nothing(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("print('hi')\n")

        survey = analyze_repository(tmp_path)

        assert survey.repository_tooling == {}


class TestUndeclaredBoundaries:
    """A boundary is a boundary whether or not it declares itself in a format we parse."""

    @staticmethod
    def _make_repo(root: Path) -> None:
        # The product: an app tree with no manifest this walk recognises.
        app = root / "trival-app"
        (app / "src").mkdir(parents=True)
        (app / "ios" / "Runner.xcodeproj").mkdir(parents=True)
        (app / "ios" / "Runner.xcodeproj" / "project.pbxproj").write_text("// xcode\n")
        (app / "ios" / "Podfile").write_text("platform :ios\n")
        vendored = app / "ios" / "Pods" / "LeftThing"
        vendored.mkdir(parents=True)
        for i in range(30):
            (vendored / f"Vendored{i}.h").write_text("/* vendored */\n")
        for i in range(30):
            (app / "src" / f"feature{i}.ts").write_text("export const x = 1\n")

    def test_candidate_is_proposed_not_promoted(self, tmp_path):
        """Without a judge, the app directory yields no module and no invented boundary."""
        self._make_repo(tmp_path)

        survey = analyze_repository(tmp_path)

        assert survey.modules == []
        subjects = {u.subject_path for u in survey.unmade_judgements}
        assert "trival-app" in subjects
        # The report says plainly which judgement was not made
        gap = next(u for u in survey.unmade_judgements if u.subject_path == "trival-app")
        assert "no agent" in gap.reason
        # and it never promotes the candidate on arithmetic alone
        assert not any("trival-app" in m.paths for m in survey.modules)

    def test_judge_can_accept_an_undeclared_boundary(self, tmp_path):
        self._make_repo(tmp_path)
        judge = _recording_judge([
            _verdict(
                "undeclared-boundary:trival-app", "module",
                reason="iOS/Android application, the repository's product",
                cited=("trival-app/src/feature0.ts", "trival-app/ios/Podfile"),
            ),
        ])

        survey = analyze_repository(tmp_path, judge=judge)

        assert [m.paths for m in survey.modules] == [["trival-app"]]
        assert survey.modules[0].origin == "agent-judgement"
        assert "typescript" in survey.modules[0].languages
        # The vendored tree inside the accepted module contributes no language
        assert "cpp" not in survey.modules[0].languages
        assert survey.agent_consulted

    def test_judge_verdict_cannot_rest_on_vendored_or_imagined_files(self, tmp_path):
        """Citations must be real files of the subject's own source — the reader
        has to be able to go and look at them."""
        self._make_repo(tmp_path)
        vendored_judge = _recording_judge([
            _verdict(
                "undeclared-boundary:trival-app", "module",
                cited=("trival-app/ios/Pods/LeftThing/Vendored0.h",),
            ),
        ])
        survey = analyze_repository(tmp_path, judge=vendored_judge)
        assert survey.modules == []
        gap = survey.unmade_judgements[0]
        assert "own source" in gap.reason

        imagined_judge = _recording_judge([
            _verdict(
                "undeclared-boundary:trival-app", "module",
                cited=("trival-app/src/nonexistent-feature.ts",),
            ),
        ])
        survey = analyze_repository(tmp_path, judge=imagined_judge)
        assert survey.modules == []
        assert "own source" in survey.unmade_judgements[0].reason

    def test_subject_relative_citations_are_anchored_to_the_subject(self, tmp_path):
        """'src/feature0.ts' for subject 'trival-app' means 'trival-app/src/feature0.ts'."""
        self._make_repo(tmp_path)
        judge = _recording_judge([
            _verdict(
                "undeclared-boundary:trival-app", "module",
                reason="the application ships here",
                cited=("src/feature0.ts", "ios/Podfile"),
            ),
        ])

        survey = analyze_repository(tmp_path, judge=judge)

        assert [m.paths for m in survey.modules] == [["trival-app"]]
        record = survey.judgements[0]
        assert record.cited_files == [
            "trival-app/src/feature0.ts", "trival-app/ios/Podfile",
        ]

    def test_abstention_is_honest_not_guessed(self, tmp_path):
        self._make_repo(tmp_path)
        judge = _recording_judge([
            _verdict(
                "undeclared-boundary:trival-app", "abstain",
                reason="cannot tell from a file listing alone",
            ),
        ])

        survey = analyze_repository(tmp_path, judge=judge)

        assert survey.modules == []
        assert survey.judgements == []
        assert "abstained" in survey.unmade_judgements[0].reason

    def test_root_manifest_means_no_candidates(self, tmp_path):
        """A root package declaration covers its tree; src/ is internals, not a boundary."""
        (tmp_path / "package.json").write_text('{"name": "site"}')
        src = tmp_path / "src"
        src.mkdir()
        for i in range(35):
            (src / f"page{i}.svelte").write_text("<h1>x</h1>")

        survey = analyze_repository(tmp_path)

        assert survey.modules == []
        assert survey.unmade_judgements == []
        assert "svelte" in survey.languages

    def test_scaffolding_dirs_are_not_candidates_when_claimed(self, tmp_path):
        """Directories that already carry a manifest are judged as modules, not candidates."""
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "package.json").write_text('{"name": "docs"}')
        for i in range(30):
            (docs / f"page{i}.js").write_text("// js\n")

        survey = analyze_repository(tmp_path)

        kinds = {u.subject_path: u.kind for u in survey.unmade_judgements}
        assert kinds.get("docs") == "boundary-role"


class TestAgentBoundaryRole:
    """Is a manifest-backed work the product, or scaffolding that hosts a doc site or test harness?"""

    @staticmethod
    def _make_deskflow_shape(root: Path) -> None:
        # Two real products and two works that only host tooling.
        for name in ("app", "lib", "docs", "tests"):
            d = root / name
            d.mkdir()
            (d / "package.json").write_text(json.dumps(
                {"name": name, "scripts": {"test": "vitest run"}}
            ))
            (d / "main.ts").write_text("export const x = 1\n")

    def test_scaffolding_verdict_removes_the_module(self, tmp_path):
        self._make_deskflow_shape(tmp_path)
        judge = _recording_judge([
            _verdict("boundary-role:app", "product", cited=("app/package.json",)),
            _verdict("boundary-role:lib", "product", cited=("lib/package.json",)),
            _verdict(
                "boundary-role:docs", "scaffolding",
                reason="a doc site, not shipped",
                cited=("docs/package.json",),
            ),
            _verdict(
                "boundary-role:tests", "scaffolding",
                reason="a test harness around the product",
                cited=("tests/package.json",),
            ),
        ])

        survey = analyze_repository(tmp_path, judge=judge)

        assert {m.module_id for m in survey.modules} == {"app", "lib"}
        assert len(survey.modules) == 2
        assert {r.subject_path for r in survey.judgements if r.verdict == "scaffolding"} == {
            "docs", "tests",
        }
        # A reader can disagree with a specific file: verdicts name their citations
        docs_record = next(r for r in survey.judgements if r.subject_path == "docs")
        assert docs_record.cited_files == ["docs/package.json"]

    def test_boundary_count_matches_listed_modules_after_scaffolding(self, tmp_path):
        """The summary counts what was concluded, and set-asides are stated separately."""
        self._make_deskflow_shape(tmp_path)
        judge = _recording_judge([
            _verdict("boundary-role:app", "product", cited=("app/package.json",)),
            _verdict("boundary-role:lib", "product", cited=("lib/package.json",)),
            _verdict(
                "boundary-role:docs", "scaffolding",
                reason="a doc site, not shipped",
                cited=("docs/package.json",),
            ),
            _verdict(
                "boundary-role:tests", "scaffolding",
                reason="a test harness around the product",
                cited=("tests/package.json",),
            ),
        ])

        survey = analyze_repository(tmp_path, judge=judge)

        boundary_finding = next(
            f for f in survey.findings if "module boundary(s)" in f.evidence[0]
        )
        stated_count = int(
            boundary_finding.evidence[0].split()[1]
        )
        assert stated_count == len(survey.modules) == 2

        scaffolding_finding = next(
            f for f in survey.findings
            if f.message.startswith("Manifest-backed works classified as scaffolding")
        )
        assert scaffolding_finding.evidence[0].startswith("2 candidate(s) set aside")

    def test_boundary_count_matches_listed_modules_without_scaffolding(self, tmp_path):
        """No set-aside line when nothing was judged scaffolding; the count still matches."""
        self._make_deskflow_shape(tmp_path)
        judge = _recording_judge([
            _verdict("boundary-role:app", "product", cited=("app/package.json",)),
            _verdict("boundary-role:lib", "product", cited=("lib/package.json",)),
            _verdict("boundary-role:docs", "product", cited=("docs/package.json",)),
            _verdict("boundary-role:tests", "product", cited=("tests/package.json",)),
        ])

        survey = analyze_repository(tmp_path, judge=judge)

        boundary_finding = next(
            f for f in survey.findings if "module boundary(s)" in f.evidence[0]
        )
        assert boundary_finding.evidence == ["Found 4 module boundary(s)"]
        assert len(survey.modules) == 4

    def test_dossier_is_grounded_in_gathered_evidence(self, tmp_path):
        """The judge is shown a digest the deterministic pass built — bounded listings,
        manifest summaries, source histograms — not sent to explore."""
        self._make_deskflow_shape(tmp_path)
        judge = _recording_judge([])

        analyze_repository(tmp_path, judge=judge)

        assert len(judge.calls) == 1
        dossier = judge.calls[0]
        subjects = {s["id"]: s for s in dossier["subjects"]}
        docs = subjects["boundary-role:docs"]
        assert docs["question"]
        assert "docs/package.json" in docs["evidence"]["manifests"]
        assert docs["evidence"]["own_source_file_count"] == 1  # main.ts; manifests don't count
        assert "repository" in dossier
        assert "top_level" in dossier["repository"]

    def test_missing_verdict_keeps_deterministic_result(self, tmp_path):
        self._make_deskflow_shape(tmp_path)
        judge = _recording_judge([
            _verdict("boundary-role:app", "product", cited=("app/package.json",)),
        ])

        survey = analyze_repository(tmp_path, judge=judge)

        # All four stand: only judgements that were made change anything
        assert len(survey.modules) == 4
        unmade_paths = {u.subject_path for u in survey.unmade_judgements}
        assert {"lib", "docs", "tests"} <= unmade_paths

    def test_judge_exception_degrades_to_deterministic(self, tmp_path):
        self._make_deskflow_shape(tmp_path)

        def judge(dossier):
            raise RuntimeError("provider down")

        survey = analyze_repository(tmp_path, judge=judge)

        assert len(survey.modules) == 4
        assert all("provider down" in u.reason for u in survey.unmade_judgements)
        assert not survey.agent_consulted

    def test_unparsable_verdicts_recorded_as_not_made(self, tmp_path):
        self._make_deskflow_shape(tmp_path)
        survey = analyze_repository(tmp_path, judge=lambda dossier: None)

        assert len(survey.modules) == 4
        assert len(survey.unmade_judgements) == 4
        assert all(u.reason for u in survey.unmade_judgements)

    def test_bogus_verdict_string_is_not_applied(self, tmp_path):
        self._make_deskflow_shape(tmp_path)
        judge = _recording_judge([
            {"subject": "boundary-role:docs", "verdict": "probably-fine",
             "reason": "hunch", "cited_files": ["docs/package.json"]},
        ])

        survey = analyze_repository(tmp_path, judge=judge)

        assert any(m.module_id == "docs" for m in survey.modules)
        gap = next(u for u in survey.unmade_judgements if u.subject_path == "docs")
        assert "unrecognized verdict" in gap.reason

    def test_empty_citations_are_not_attributable(self, tmp_path):
        self._make_deskflow_shape(tmp_path)
        judge = _recording_judge([
            {"subject": "boundary-role:docs", "verdict": "scaffolding",
             "reason": "looks like docs", "cited_files": []},
        ])

        survey = analyze_repository(tmp_path, judge=judge)

        assert any(m.module_id == "docs" for m in survey.modules)
        gap = next(u for u in survey.unmade_judgements if u.subject_path == "docs")
        assert "cited no evidence" in gap.reason

    def test_judged_scaffolding_is_reported_not_inventedin_requirements(self, tmp_path):
        """Verdicts classify what exists; they never demand governance."""
        self._make_deskflow_shape(tmp_path)
        judge = _recording_judge([
            _verdict("boundary-role:docs", "scaffolding", cited=("docs/package.json",)),
            _verdict("boundary-role:tests", "scaffolding", cited=("tests/package.json",)),
        ])

        survey = analyze_repository(tmp_path, judge=judge)

        for finding in survey.findings:
            lowered = finding.message.lower()
            assert "violat" not in lowered
            assert "missing" not in lowered
            assert "require" not in lowered

    def test_survey_still_works_with_no_subjects_no_call(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'")
        (tmp_path / "main.py").write_text("print('hi')")
        called = []

        def judge(dossier):
            called.append(dossier)
            return {"verdicts": []}

        survey = analyze_repository(tmp_path, judge=judge)

        assert called == []
        assert survey.modules == []
        assert survey.unmade_judgements == []


class TestDossierManifestSummaries:
    """The judge sees a structured digest of every manifest format we parse."""

    def test_manifests_are_summarized_for_each_stack(self, tmp_path):
        (tmp_path / "py").mkdir()
        (tmp_path / "py" / "pyproject.toml").write_text(
            "[project]\nname = 'svc'\ndependencies = ['flask']\n"
        )
        (tmp_path / "rs").mkdir()
        (tmp_path / "rs" / "Cargo.toml").write_text("[package]\nname = 'tool'\n")
        (tmp_path / "go").mkdir()
        (tmp_path / "go" / "go.mod").write_text("module example.dev/svc\n\ngo 1.22\n")
        (tmp_path / "jvm").mkdir()
        (tmp_path / "jvm" / "pom.xml").write_text(
            "<project><artifactId>service</artifactId></project>"
        )
        (tmp_path / "big").mkdir()
        (tmp_path / "big" / "package.json").write_text(json.dumps(
            {"name": "big", "description": "x" * 4000}
        ))

        judge = _recording_judge([])
        analyze_repository(tmp_path, judge=judge)

        subjects = {s["id"]: s for s in judge.calls[0]["subjects"]}
        assert "svc" in subjects["boundary-role:py"]["evidence"]["manifests"][
            "py/pyproject.toml"
        ]
        assert "tool" in subjects["boundary-role:rs"]["evidence"]["manifests"][
            "rs/Cargo.toml"
        ]
        assert "example.dev/svc" in subjects["boundary-role:go"]["evidence"]["manifests"][
            "go/go.mod"
        ]
        assert "service" in subjects["boundary-role:jvm"]["evidence"]["manifests"][
            "jvm/pom.xml"
        ]
        big = subjects["boundary-role:big"]["evidence"]["manifests"]["big/package.json"]
        assert len(big) <= _DOSSIER_TRUNC_NOTE_LIMIT
        assert "truncated" in big


class TestJudgementWritesNothing:
    def test_analyzer_with_judge_writes_nothing(self, tmp_path):
        TestGrownNotScaffoldedRepository._make_repo(tmp_path)
        judge = _recording_judge([
            _verdict("boundary-role:api.droptrack.io", "product",
                     cited=("api.droptrack.io/package.json",)),
        ])

        before = {p.relative_to(tmp_path) for p in tmp_path.rglob("*")}
        analyze_repository(tmp_path, judge=judge)
        after = {p.relative_to(tmp_path) for p in tmp_path.rglob("*")}

        assert before == after
        assert not (tmp_path / ".snodo").exists()

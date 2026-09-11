"""Tests for repository survey analyzer.

FILE: tests/survey/test_analyzer.py

Tests that the analyzer correctly:
1. Discovers module boundaries from workspace markers
2. Detects languages and tooling per module
3. Identifies test commands from marker files
4. Locates decision records
5. Never infers requirements from absence of practices
"""

import tempfile
from pathlib import Path

from snodo.survey.analyzer import analyze_repository


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

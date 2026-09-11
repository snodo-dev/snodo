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
            (project_root / "pyproject.toml").write_text("[project]\nname = 'test'")

            # Analyze
            survey = analyze_repository(project_root)

            # Single package detected
            assert len(survey.modules) == 0  # No workspaces, so no modules
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

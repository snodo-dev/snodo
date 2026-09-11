"""Tests for survey command.

FILE: tests/cli/test_survey_cmd.py

Integration tests for the survey command, verifying that it:
1. Analyzes repositories and proposes governance
2. Never writes to the repository
3. Correctly handles repositories with/without protocols
4. Reports findings with evidence
"""

import json
import tempfile
from pathlib import Path

from snodo.cli.commands.survey_cmd import survey_command


class TestSurveyCommand:
    """Tests for survey command integration."""

    def test_survey_single_package_repo(self):
        """Test survey on single-package repository."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)

            # Create a simple Python project
            (project_root / "src").mkdir()
            (project_root / "src" / "main.py").write_text("print('hello')")
            (project_root / "pyproject.toml").write_text("[project]\nname = 'test'")

            # Initialize git
            from git import Repo
            Repo.init(str(project_root))

            # Change to the directory and run survey
            import os
            old_cwd = os.getcwd()
            try:
                os.chdir(project_root)

                from types import SimpleNamespace
                args = SimpleNamespace(json=False)

                # Should not raise an error
                exit_code = survey_command(args)
                assert exit_code == 0

                # .snodo should not have been created
                assert not (project_root / ".snodo").exists()
            finally:
                os.chdir(old_cwd)

    def test_survey_json_output(self):
        """Test survey JSON output format."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)

            # Create a simple project
            (project_root / "src").mkdir()
            (project_root / "src" / "main.py").write_text("print('hello')")

            # Initialize git
            from git import Repo
            Repo.init(str(project_root))

            # Change to the directory and run survey
            import os
            import sys
            from io import StringIO

            old_cwd = os.getcwd()
            old_stdout = sys.stdout
            try:
                os.chdir(project_root)
                sys.stdout = StringIO()

                from types import SimpleNamespace
                args = SimpleNamespace(json=True)

                # Capture the exit code
                exit_code = survey_command(args)

                # Get the output
                output = sys.stdout.getvalue()
                assert exit_code == 0

                # Parse the JSON output
                result = json.loads(output)
                assert result["ok"] is True
                assert "analysis" in result
                assert "project_id" in result
                assert "display_name" in result
            finally:
                os.chdir(old_cwd)
                sys.stdout = old_stdout

    def test_survey_refuses_existing_protocol(self):
        """Test that survey refuses to run on repos with existing protocol."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)

            # Create .snodo directory with protocol
            snodo_dir = project_root / ".snodo"
            snodo_dir.mkdir()
            (snodo_dir / "protocol.yml").write_text("protocol: exists")

            # Initialize git
            from git import Repo
            Repo.init(str(project_root))

            # Change to the directory and run survey
            import os
            old_cwd = os.getcwd()
            try:
                os.chdir(project_root)

                from types import SimpleNamespace
                args = SimpleNamespace(json=False)

                # Should exit with error
                exit_code = survey_command(args)
                assert exit_code == 4  # EXIT_INTERNAL_ERROR
            finally:
                os.chdir(old_cwd)

    def test_survey_respects_module_boundaries(self):
        """Test that survey detects module boundaries from workspace markers."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)

            # Create npm workspaces
            import json as json_mod
            package_json = {
                "name": "monorepo",
                "workspaces": ["packages/api", "packages/web"],
            }
            (project_root / "package.json").write_text(json_mod.dumps(package_json))

            # Create workspace directories
            (project_root / "packages" / "api").mkdir(parents=True)
            (project_root / "packages" / "web").mkdir(parents=True)

            # Initialize git
            from git import Repo
            Repo.init(str(project_root))

            # Change to the directory and run survey
            import os
            old_cwd = os.getcwd()
            try:
                os.chdir(project_root)

                from types import SimpleNamespace
                args = SimpleNamespace(json=True)

                import sys
                from io import StringIO
                sys.stdout = StringIO()

                exit_code = survey_command(args)
                output = sys.stdout.getvalue()

                assert exit_code == 0
                result = json.loads(output)

                # Should detect modules
                assert len(result["analysis"]["modules"]) > 0
            finally:
                os.chdir(old_cwd)

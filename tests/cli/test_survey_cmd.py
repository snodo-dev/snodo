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

    def test_survey_fails_when_the_protocol_cannot_be_loaded(self):
        """A protocol that cannot be parsed is a failure: there is nothing to compare.

        This is the one governed case that still exits non-zero. An *unreadable*
        protocol is not "a repository that is governed" — reporting it with the
        pass code would hide a real fault, and reporting a healthy governed
        repository with the fault code was the misclassification this command
        used to make (see tests/cli/test_survey_drift_cmd.py).
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)

            # Create .snodo directory with an unloadable protocol
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


class TestSurveyAgentPlumbing:
    """The judge is an adapter over the recon machinery, not a second path."""

    def test_parse_verdicts_from_prose_wrapped_json(self):
        from snodo.cli.commands.survey_cmd import _parse_verdicts

        text = (
            "I reviewed the dossier.\n"
            '```json\n{"judgements": [{"subject": "boundary-role:docs", '
            '"verdict": "scaffolding", "reason": "doc site", '
            '"cited_files": ["docs/package.json"]}]}\n```'
        )
        verdicts = _parse_verdicts(text)
        assert verdicts == [{
            "subject": "boundary-role:docs",
            "verdict": "scaffolding",
            "reason": "doc site",
            "cited_files": ["docs/package.json"],
        }]

    def test_parse_verdicts_rejects_unusable_replies(self):
        from snodo.cli.commands.survey_cmd import _parse_verdicts

        assert _parse_verdicts("The repository looks fine to me.") is None
        assert _parse_verdicts('{"notes": "no judgements key"}') is None
        assert _parse_verdicts("") is None

    def test_auto_mode_without_configured_agent_builds_no_judge(self, tmp_path):
        # conftest isolates $HOME, so snodo's config has no provider keys
        from snodo.cli.commands.survey_cmd import build_survey_judge

        assert build_survey_judge(tmp_path, "auto") is None
        assert build_survey_judge(tmp_path, None) is None
        assert build_survey_judge(tmp_path, "off") is None

    def test_force_mode_builds_a_judge_bound_to_the_configured_model(self, tmp_path):
        from snodo.cli.commands.survey_cmd import build_survey_judge

        judge = build_survey_judge(tmp_path, "force")
        assert judge is not None
        assert isinstance(judge.model, str) and judge.model


class TestSurveyCommandWithJudge:
    """Survey output distinguishes made judgements from unmade ones."""

    @staticmethod
    def _make_repo(root):
        import json as json_mod
        from git import Repo
        Repo.init(str(root))
        package_json = {
            "name": "monorepo",
            "workspaces": ["app", "docs", "tests"],
        }
        (root / "package.json").write_text(json_mod.dumps(package_json))
        for name in ("app", "docs", "tests"):
            d = root / name
            d.mkdir()
            (d / "package.json").write_text(json_mod.dumps({"name": name}))
            (d / "main.ts").write_text("export const x = 1")

    def test_judged_scaffolding_disappears_from_modules(self, tmp_path, monkeypatch):
        import io
        import json as json_mod
        from contextlib import redirect_stdout
        from types import SimpleNamespace

        from snodo.cli.commands import survey_cmd
        self._make_repo(tmp_path)
        monkeypatch.chdir(tmp_path)

        def stub_judge(dossier):
            verdicts = []
            for s in dossier["subjects"]:
                if s["path"] in ("docs", "tests"):
                    verdicts.append({
                        "subject": s["id"], "verdict": "scaffolding",
                        "reason": "hosts docs and a test harness",
                        "cited_files": [f"{s['path']}/package.json"],
                    })
                else:
                    verdicts.append({
                        "subject": s["id"], "verdict": "product",
                        "reason": "the shipped application",
                        "cited_files": [f"{s['path']}/package.json"],
                    })
            return {"verdicts": verdicts}

        monkeypatch.setattr(survey_cmd, "build_survey_judge", lambda root, mode: stub_judge)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = survey_cmd.survey_command(SimpleNamespace(json=True, agent="auto"))
        assert rc == 0
        payload = json_mod.loads(buf.getvalue())
        analysis = payload["analysis"]
        assert {m["module_id"] for m in analysis["modules"]} == {"app"}
        assert len(analysis["judgements"]) == 3
        assert analysis["unmade_judgements"] == []
        assert analysis["agent_consulted"] is True

    def test_failed_agent_call_keeps_deterministic_result_and_says_so(self, tmp_path, monkeypatch, capsys):
        from types import SimpleNamespace

        from snodo.cli.commands import survey_cmd
        self._make_repo(tmp_path)
        monkeypatch.chdir(tmp_path)

        def broken_judge(dossier):
            raise RuntimeError("provider unreachable")

        monkeypatch.setattr(survey_cmd, "build_survey_judge", lambda root, mode: broken_judge)
        rc = survey_cmd.survey_command(SimpleNamespace(json=False, agent="force"))
        out = capsys.readouterr().out

        assert rc == 0
        # deterministic result stands: all three modules still reported
        for name in ("app", "docs", "tests"):
            assert f"• {name}" in out
        assert "Not made" in out
        assert "provider unreachable" in out

    def test_no_agent_reports_which_judgements_were_not_made(self, tmp_path, monkeypatch, capsys):
        from types import SimpleNamespace

        from snodo.cli.commands import survey_cmd
        self._make_repo(tmp_path)
        monkeypatch.chdir(tmp_path)

        monkeypatch.setattr(
            survey_cmd, "build_survey_judge", lambda root, mode: None
        )
        rc = survey_cmd.survey_command(SimpleNamespace(json=False, agent="off"))
        out = capsys.readouterr().out

        assert rc == 0
        assert "No agent consulted" in out
        assert "deterministic-only run" in out

    def test_survey_writes_nothing_with_a_judge(self, tmp_path, monkeypatch):
        from types import SimpleNamespace

        from snodo.cli.commands import survey_cmd
        self._make_repo(tmp_path)
        Repo = __import__("git").Repo
        Repo.init(str(tmp_path))
        monkeypatch.setattr(
            survey_cmd, "build_survey_judge",
            lambda root, mode: (lambda dossier: {"verdicts": []}),
        )
        import os
        old = os.getcwd()
        os.chdir(tmp_path)
        try:
            survey_cmd.survey_command(SimpleNamespace(json=True, agent="auto"))
        finally:
            os.chdir(old)
        assert not (tmp_path / ".snodo").exists()
        assert not (tmp_path / ".snodo" / "recons").exists()


class TestJudgeAdapter:
    """The judge body: recon call in, verdicts out, failures reported not hidden."""

    @staticmethod
    def _result(error=None, result=""):
        from snodo.recon import ReconResult
        return ReconResult(agent="survey-judge", model="m", result=result, error=error)

    def test_judge_sends_prompt_with_dossier_and_parses_verdicts(self, tmp_path, monkeypatch):
        from snodo.cli.commands import survey_cmd

        judge = survey_cmd.build_survey_judge(tmp_path, "force")
        captured = {}

        def fake_call_agent(project_root, model, query, paths, agent_label, max_turns):
            captured.update(
                project_root=project_root, model=model, query=query,
                agent_label=agent_label, max_turns=max_turns,
            )
            return self._result(result='{"judgements": [{"subject": "x", "verdict": "product"}]}')

        monkeypatch.setattr("snodo.recon.call_agent", fake_call_agent)
        outcome = judge({"repository": {"name": "proj"}, "subjects": [{"id": "x"}]})

        assert outcome == {"verdicts": [{"subject": "x", "verdict": "product"}]}
        assert captured["agent_label"] == "survey-judge"
        assert captured["project_root"] == str(tmp_path)
        assert captured["max_turns"] == survey_cmd._JUDGE_MAX_TURNS
        # the dossier is inline — the agent classifies given evidence
        assert '"name": "proj"' in captured["query"]
        assert "CLASSIFY, not to" in captured["query"]

    def test_judge_reports_call_error_as_no_verdicts_with_reason(self, tmp_path, monkeypatch):
        from snodo.cli.commands import survey_cmd

        judge = survey_cmd.build_survey_judge(tmp_path, "force")
        monkeypatch.setattr(
            "snodo.recon.call_agent",
            lambda *a, **k: self._result(error="429 rate limited"),
        )
        outcome = judge({"subjects": []})
        assert "verdicts" not in outcome or outcome["verdicts"] is None
        assert "429 rate limited" in outcome["reason"]

    def test_judge_reports_unparseable_reply_as_no_verdicts(self, tmp_path, monkeypatch):
        from snodo.cli.commands import survey_cmd

        judge = survey_cmd.build_survey_judge(tmp_path, "force")
        monkeypatch.setattr(
            "snodo.recon.call_agent",
            lambda *a, **k: self._result(result="That looks like a monorepo to me!"),
        )
        outcome = judge({"subjects": []})
        assert "no parseable" in outcome["reason"]

    def test_auto_mode_uses_a_recon_configured_model_with_a_key(self, tmp_path):
        from snodo.config import ConfigManager
        from snodo.cli.commands.survey_cmd import build_survey_judge

        cfg_dir = ConfigManager().config_dir
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "config.yml").write_text(
            "model: some/unknown-default\n"
            "providers:\n  deepseek:\n    api_key: test-key-not-real\n"
            "llm:\n  recon:\n    models: [deepseek/deepseek-v4-flash]\n    num_agents: 1\n"
        )

        judge = build_survey_judge(tmp_path, "auto")
        assert judge is not None
        assert judge.model == "deepseek/deepseek-v4-flash"

    def test_survey_judge_falls_back_to_second_model_on_silence_or_fault(self, tmp_path, monkeypatch):
        from snodo.config import ConfigManager
        from snodo.cli.commands import survey_cmd

        cfg_dir = ConfigManager().config_dir
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "config.yml").write_text(
            "model: some/unknown-default\n"
            "providers:\n"
            "  deepseek:\n    api_key: test-key-1\n"
            "  openai:\n    api_key: test-key-2\n"
            "llm:\n  recon:\n    models: [deepseek/first-disengaging, openai/second-good]\n"
        )

        judge = survey_cmd.build_survey_judge(tmp_path, "auto")
        assert judge is not None

        calls = []

        def fake_call_agent(project_root, model, query, paths, agent_label, max_turns):
            calls.append(model)
            if model == "deepseek/first-disengaging":
                return self._result(error="Agent returned empty result", result="")
            return self._result(
                result='{"judgements": [{"subject": "x", "verdict": "product", "reason": "ok", "cited_files": ["f"]}]}'
            )

        monkeypatch.setattr("snodo.recon.call_agent", fake_call_agent)
        outcome = judge({"subjects": [{"id": "x"}]})

        assert outcome == {"verdicts": [{"subject": "x", "verdict": "product", "reason": "ok", "cited_files": ["f"]}]}
        assert calls == ["deepseek/first-disengaging", "openai/second-good"]
        assert judge.model == "openai/second-good"

    def test_survey_judge_stops_when_first_model_answers(self, tmp_path, monkeypatch):
        from snodo.config import ConfigManager
        from snodo.cli.commands import survey_cmd

        cfg_dir = ConfigManager().config_dir
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "config.yml").write_text(
            "model: some/unknown-default\n"
            "providers:\n"
            "  deepseek:\n    api_key: test-key-1\n"
            "  openai:\n    api_key: test-key-2\n"
            "llm:\n  recon:\n    models: [deepseek/first-working, openai/second-unused]\n"
        )

        judge = survey_cmd.build_survey_judge(tmp_path, "auto")
        assert judge is not None

        calls = []

        def fake_call_agent(project_root, model, query, paths, agent_label, max_turns):
            calls.append(model)
            return self._result(
                result='{"judgements": [{"subject": "x", "verdict": "product", "reason": "ok", "cited_files": ["f"]}]}'
            )

        monkeypatch.setattr("snodo.recon.call_agent", fake_call_agent)
        outcome = judge({"subjects": [{"id": "x"}]})

        assert "verdicts" in outcome
        assert calls == ["deepseek/first-working"]
        assert judge.model == "deepseek/first-working"


class TestSurveyHumanOutputSections:
    def test_agent_consulted_without_accepted_verdicts_is_said_plainly(self, tmp_path, monkeypatch, capsys):
        import json as json_mod
        from types import SimpleNamespace

        from git import Repo

        from snodo.cli.commands import survey_cmd
        Repo.init(str(tmp_path))
        (tmp_path / "package.json").write_text(json_mod.dumps(
            {"name": "mono", "workspaces": ["packages/tooling"]}
        ))
        (tmp_path / "packages" / "tooling").mkdir(parents=True)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            survey_cmd, "build_survey_judge",
            lambda root, mode: (lambda dossier: {"verdicts": ["garbage", 42]}),
        )

        rc = survey_cmd.survey_command(SimpleNamespace(json=False, agent="force"))
        out = capsys.readouterr().out
        assert rc == 0
        assert "no verdict was accepted" in out
        assert "Not made" in out
        # declared workspace modules carry no manifest: origin is shown
        assert "Origin: declaration" in out


class TestWaitingIndicator:
    """The command says what it is waiting on, without becoming output.

    The agent call can take tens of seconds; before this the command was silent
    and indistinguishable from a hang. The indicator is decoration on stderr,
    off under --json and when stdout is not a terminal, and it must not disturb
    the diagnostic an empty agent result prints.
    """

    @staticmethod
    def _make_repo(root):
        import json as json_mod
        from git import Repo
        Repo.init(str(root))
        (root / "package.json").write_text(json_mod.dumps(
            {"name": "mono", "workspaces": ["app"]}
        ))
        (root / "app").mkdir()
        (root / "app" / "package.json").write_text(json_mod.dumps({"name": "app"}))
        (root / "app" / "main.ts").write_text("export const x = 1")

    class _Tty:
        """A stdout stand-in whose isatty() is True, with the bytes written."""

        def __init__(self):
            self.value = ""

        def write(self, text):
            self.value += text

        def flush(self):
            pass

        def isatty(self):
            return True

    def _on_a_tty(self, monkeypatch):
        """Route stdout (a tty) and stderr to buffers, and return both.

        ``survey_cmd`` reaches the streams through the shared ``sys`` module,
        so patching there is the production path. Returns (stdout, stderr).
        """
        import io

        from snodo.cli.commands import survey_cmd
        stdout = self._Tty()
        stderr = io.StringIO()
        monkeypatch.setattr(survey_cmd.sys, "stdout", stdout)
        monkeypatch.setattr(survey_cmd.sys, "stderr", stderr)
        return stdout, stderr

    def test_indicator_names_the_wait_and_clears_while_the_call_is_in_flight(
        self, tmp_path, monkeypatch
    ):
        from types import SimpleNamespace

        from snodo.cli.commands import survey_cmd
        self._make_repo(tmp_path)
        monkeypatch.chdir(tmp_path)

        shown = {}

        def stub_judge(dossier):
            shown["during"] = stderr.getvalue()
            return {"verdicts": []}

        monkeypatch.setattr(survey_cmd, "build_survey_judge", lambda root, mode: stub_judge)
        stub_judge.model = "deepseek/deepseek-flash"
        stdout, stderr = self._on_a_tty(monkeypatch)

        rc = survey_cmd.survey_command(SimpleNamespace(json=False, agent="force"))

        assert rc == 0
        # While the call was in flight, the indicator named what it waits on.
        assert "Waiting for the survey agent" in shown["during"]
        assert "deepseek/deepseek-flash" in shown["during"]
        # It wrote to stderr, never to stdout, and erased itself at the end:
        # the last thing written is a carriage return with no message.
        assert "Waiting for the survey agent" not in stdout.value
        assert stderr.getvalue().endswith("\r")

    def test_stdout_is_byte_identical_on_a_tty_and_off_it(
        self, tmp_path, monkeypatch
    ):
        """Decoration never reaches stdout: same report, tty or pipe."""
        import io
        from types import SimpleNamespace

        from snodo.cli.commands import survey_cmd
        self._make_repo(tmp_path)
        monkeypatch.chdir(tmp_path)

        def verdicts(dossier):
            return {"verdicts": []}

        monkeypatch.setattr(survey_cmd, "build_survey_judge", lambda root, mode: verdicts)
        verdicts.model = "deepseek/deepseek-flash"

        def run(stdout):
            stderr = io.StringIO()
            monkeypatch.setattr(survey_cmd.sys, "stdout", stdout)
            monkeypatch.setattr(survey_cmd.sys, "stderr", stderr)
            rc = survey_cmd.survey_command(SimpleNamespace(json=False, agent="force"))
            return rc, stdout.value, stderr.getvalue()

        tty_out = self._Tty()
        rc_tty, out_tty, err_tty = run(tty_out)

        class _Pipe:
            def __init__(self):
                self.value = ""

            def write(self, text):
                self.value += text

            def flush(self):
                pass

            def isatty(self):
                return False

        pipe_out = _Pipe()
        rc_pipe, out_pipe, err_pipe = run(pipe_out)

        assert rc_tty == rc_pipe == 0
        # The project id is generated per run; compare the report proper.
        import re

        def strip_id(text):
            return re.sub(r"^Project ID: .*$", "Project ID: <ID>", text, flags=re.MULTILINE)

        assert strip_id(out_tty) == strip_id(out_pipe), "the report is identical on a tty and off it"
        assert "Waiting for the survey agent" in err_tty
        assert "Waiting for the survey agent" not in err_pipe

    def test_json_output_carries_no_indicator(self, tmp_path, monkeypatch, capsys):
        from types import SimpleNamespace

        from snodo.cli.commands import survey_cmd
        self._make_repo(tmp_path)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            survey_cmd, "build_survey_judge",
            lambda root, mode: (lambda dossier: {"verdicts": []}),
        )
        # Even on a tty, --json must not decorate stderr.
        monkeypatch.setattr(survey_cmd.sys.stdout, "isatty", lambda: True)

        rc = survey_cmd.survey_command(SimpleNamespace(json=True, agent="force"))
        captured = capsys.readouterr()

        assert rc == 0
        assert json.loads(captured.out)["ok"] is True
        assert "Waiting for the survey agent" not in captured.err

    def test_non_tty_stdout_carries_no_indicator(self, tmp_path, monkeypatch, capsys):
        from types import SimpleNamespace

        from snodo.cli.commands import survey_cmd
        self._make_repo(tmp_path)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            survey_cmd, "build_survey_judge",
            lambda root, mode: (lambda dossier: {"verdicts": []}),
        )
        assert not survey_cmd.sys.stdout.isatty()

        rc = survey_cmd.survey_command(SimpleNamespace(json=False, agent="force"))
        captured = capsys.readouterr()

        assert rc == 0
        assert "Waiting for the survey agent" not in captured.err
        assert "Waiting for the survey agent" not in captured.out
        assert "Repository Analysis:" in captured.out

    def test_empty_agent_result_diagnostic_survives(
        self, tmp_path, monkeypatch, capsys, caplog
    ):
        """The recon empty-result warning is still emitted, indicator and all."""
        import logging
        from types import SimpleNamespace

        from snodo.cli.commands import survey_cmd
        self._make_repo(tmp_path)
        monkeypatch.chdir(tmp_path)

        def empty_judge(dossier):
            logging.getLogger("snodo.recon").warning(
                "Recon agent returned empty result on model=deepseek/deepseek-flash "
                "— possible model disengagement or auth issue"
            )
            return {"reason": "Agent returned empty result"}

        monkeypatch.setattr(survey_cmd, "build_survey_judge", lambda root, mode: empty_judge)
        monkeypatch.setattr(survey_cmd.sys.stdout, "isatty", lambda: True)

        with caplog.at_level(logging.WARNING, logger="snodo.recon"):
            rc = survey_cmd.survey_command(SimpleNamespace(json=False, agent="force"))
        captured = capsys.readouterr()

        assert rc == 0
        assert any(
            "Recon agent returned empty result on model=deepseek/deepseek-flash"
            in record.getMessage()
            for record in caplog.records
        )
        assert "Not made" in captured.out


class TestTestCommandSummaryScope:
    """The summary names the scope it speaks for: repository root, not modules."""

    def test_monorepo_summary_does_not_read_as_no_tests(
        self, tmp_path, monkeypatch, capsys
    ):
        import json as json_mod
        from types import SimpleNamespace

        from git import Repo

        from snodo.cli.commands import survey_cmd
        Repo.init(str(tmp_path))
        (tmp_path / "package.json").write_text(json_mod.dumps(
            {"name": "mono", "workspaces": ["app", "lib"]}
        ))
        for name in ("app", "lib"):
            d = tmp_path / name
            d.mkdir()
            (d / "package.json").write_text(json_mod.dumps(
                {"name": name, "scripts": {"test": "vitest run"}}
            ))
            (d / "main.ts").write_text("export const x = 1")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(survey_cmd, "build_survey_judge", lambda root, mode: None)

        rc = survey_cmd.survey_command(SimpleNamespace(json=False, agent="off"))
        out = capsys.readouterr().out

        assert rc == 0
        assert "No repository-level test command" in out
        assert "Module-level test commands were confirmed" in out
        # and the module commands themselves are still shown
        assert "Test: npm test" in out

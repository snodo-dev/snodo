"""Tests for the logs command and logs watch functionality.

FILE: tests/cli/test_logs_cmd.py
"""

import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from snodo.cli.commands.logs_cmd import logs_command


def test_logs_watch_drain_loop(capsys):
    """Test that logs --watch runs the drain loop and outputs all remaining lines when status becomes terminal."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        job_dir = Path(tmp_dir) / ".snodo" / "jobs" / "j_test"
        job_dir.mkdir(parents=True)
        log_file = job_dir / "stdout.log"
        # Write some initial lines
        log_file.write_text("line1\nline2\n")

        # Mock the require_project_root to return our tmp_dir
        with patch("snodo.infrastructure.paths.require_project_root", return_value=tmp_dir):
            mock_manager = MagicMock()
            # mock_manager._job_dir returns the correct dir
            mock_manager._job_dir.return_value = job_dir

            # To verify the drain loop runs, we append "line3\n" to the file
            # during the mock get_status call and return completed.
            def mock_get_status(job_id):
                log_file.write_text("line1\nline2\nline3\n")
                return {"status": "completed"}

            mock_manager.get_status.side_effect = mock_get_status

            with patch("snodo.jobs.JobManager", return_value=mock_manager):
                args = SimpleNamespace(composite_id="j_test", watch=True)
                result = logs_command(args)

                assert result == 0
                out = capsys.readouterr().out
                # First pass reads "line1" and "line2".
                # When get_status is called, it writes "line3".
                # The drain loop should read "line3" before exiting.
                assert "line1" in out
                assert "line2" in out
                assert "line3" in out


def test_job_with_no_output_reports_rather_than_blocking(capsys):
    """Test that a job with no output reports '(no stdout output)' rather than blocking."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        job_dir = Path(tmp_dir) / ".snodo" / "jobs" / "j_empty"
        job_dir.mkdir(parents=True)
        log_file = job_dir / "stdout.log"
        log_file.write_text("")  # 0 bytes

        with patch("snodo.infrastructure.paths.require_project_root", return_value=tmp_dir):
            mock_manager = MagicMock()
            mock_manager._job_dir.return_value = job_dir
            mock_manager.get_status.return_value = {"status": "completed", "exit_code": 0}

            with patch("snodo.jobs.JobManager", return_value=mock_manager):
                args = SimpleNamespace(composite_id="j_empty", watch=True)
                result = logs_command(args)

                assert result == 0
                out = capsys.readouterr().out
                assert "(no stdout output)" in out


def test_job_with_no_output_missing_file_reports_rather_than_blocking(capsys):
    """Test that a completed job whose stdout.log was never created reports '(no stdout output)'."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        job_dir = Path(tmp_dir) / ".snodo" / "jobs" / "j_nofile"
        job_dir.mkdir(parents=True)
        # stdout.log does not exist

        with patch("snodo.infrastructure.paths.require_project_root", return_value=tmp_dir):
            mock_manager = MagicMock()
            mock_manager._job_dir.return_value = job_dir
            mock_manager.get_status.return_value = {"status": "completed", "exit_code": 0}

            with patch("snodo.jobs.JobManager", return_value=mock_manager):
                args = SimpleNamespace(composite_id="j_nofile", watch=True)
                result = logs_command(args)

                assert result == 0
                out = capsys.readouterr().out
                assert "(no stdout output)" in out


def test_following_plan_job_surfaces_children_progress(capsys):
    """Test that following a plan job surfaces its children's progress rather than an empty stream."""
    import json
    import yaml

    with tempfile.TemporaryDirectory() as tmp_dir:
        snodo_dir = Path(tmp_dir) / ".snodo"
        jobs_dir = snodo_dir / "jobs"
        plans_dir = snodo_dir / "plans"

        # 1. Setup plan definition
        plan_dir = plans_dir / "test_plan"
        plan_dir.mkdir(parents=True)
        plan_yml = {
            "name": "test_plan",
            "intent": "Deploy multi-task service",
            "waves": [
                {"id": 1, "depends_on": [], "tasks": ["1.1_models", "1.2_views"]},
            ],
        }
        (plan_dir / "plan.yml").write_text(yaml.dump(plan_yml))
        (plan_dir / "status.json").write_text(json.dumps({
            "tasks": {
                "1.1_models": {"status": "completed"},
                "1.2_views": {"status": "running"},
            }
        }))

        # 2. Setup plan job
        plan_job_dir = jobs_dir / "j_plan01"
        plan_job_dir.mkdir(parents=True)
        (plan_job_dir / "task.json").write_text(json.dumps({"plan_name": "test_plan"}))
        (plan_job_dir / "state.json").write_text(json.dumps({"status": "running", "job_type": "plan"}))
        (plan_job_dir / "stdout.log").write_text("")  # plan job has empty stdout!

        # 3. Setup child task jobs
        child1_dir = jobs_dir / "j_task01"
        child1_dir.mkdir(parents=True)
        (child1_dir / "task.json").write_text(json.dumps({
            "task_id": "1.1_models",
            "parent_job": "j_plan01",
        }))
        (child1_dir / "state.json").write_text(json.dumps({
            "status": "completed",
            "duration_seconds": 12.5,
            "created_at": 1000,
        }))

        child2_dir = jobs_dir / "j_task02"
        child2_dir.mkdir(parents=True)
        (child2_dir / "task.json").write_text(json.dumps({
            "task_id": "1.2_views",
            "parent_job": "j_plan01",
        }))
        (child2_dir / "state.json").write_text(json.dumps({
            "status": "running",
            "duration_seconds": None,
            "created_at": 1001,
        }))

        with patch("snodo.infrastructure.paths.require_project_root", return_value=tmp_dir):
            from snodo.jobs import JobManager
            mgr = JobManager(tmp_dir)

            # Sequence: first check in loop transitions child2 to completed, and plan to completed
            call_count = 0

            def mock_get_status(jid):
                nonlocal call_count
                if jid == "j_plan01":
                    call_count += 1
                    if call_count >= 2:
                        return {
                            "status": "completed",
                            "job_type": "plan",
                            "exit_code": 0,
                            "task": {"plan_name": "test_plan"},
                        }
                    return {
                        "status": "running",
                        "job_type": "plan",
                        "task": {"plan_name": "test_plan"},
                    }
                return mgr.get_status(jid)

            def mock_list_jobs():
                # Child 2 completes on second call
                c2_stat = "completed" if call_count >= 2 else "running"
                return [
                    {
                        "id": "j_plan01",
                        "status": "completed" if call_count >= 2 else "running",
                        "plan": "test_plan",
                        "parent_job": "",
                        "task_ref": "",
                        "created_at": 999,
                    },
                    {
                        "id": "j_task01",
                        "status": "completed",
                        "parent_job": "j_plan01",
                        "task_ref": "1.1_models",
                        "duration_seconds": 12.5,
                        "created_at": 1000,
                    },
                    {
                        "id": "j_task02",
                        "status": c2_stat,
                        "parent_job": "j_plan01",
                        "task_ref": "1.2_views",
                        "duration_seconds": 18.0 if c2_stat == "completed" else None,
                        "created_at": 1001,
                    },
                ]

            with patch.object(JobManager, "get_status", side_effect=mock_get_status), \
                 patch.object(JobManager, "list_jobs", side_effect=mock_list_jobs), \
                 patch("time.sleep", return_value=None):

                args = SimpleNamespace(composite_id="j_plan01", watch=True)
                res = logs_command(args)

                assert res == 0
                out = capsys.readouterr().out

                # Must surface children's progress rather than an empty stream
                assert "Following plan run j_plan01" in out
                assert "1.1_models" in out
                assert "j_task01" in out
                assert "1.2_views" in out
                assert "j_task02" in out
                assert "Plan run j_plan01 finished (completed)" in out


def test_plan_job_without_watch_explains_output_in_children(capsys):
    """Test that running snodo logs on a plan job explains that output is in its children."""
    import json
    import yaml

    with tempfile.TemporaryDirectory() as tmp_dir:
        snodo_dir = Path(tmp_dir) / ".snodo"
        jobs_dir = snodo_dir / "jobs"
        plans_dir = snodo_dir / "plans"

        plan_dir = plans_dir / "auth_plan"
        plan_dir.mkdir(parents=True)
        plan_yml = {
            "name": "auth_plan",
            "intent": "Implement OAuth",
            "waves": [{"id": 1, "depends_on": [], "tasks": ["1.1_tokens"]}],
        }
        (plan_dir / "plan.yml").write_text(yaml.dump(plan_yml))
        (plan_dir / "status.json").write_text(json.dumps({
            "tasks": {"1.1_tokens": {"status": "completed"}}
        }))

        plan_job_dir = jobs_dir / "j_plan_auth"
        plan_job_dir.mkdir(parents=True)
        (plan_job_dir / "task.json").write_text(json.dumps({"plan_name": "auth_plan"}))
        (plan_job_dir / "state.json").write_text(json.dumps({"status": "completed", "job_type": "plan"}))
        (plan_job_dir / "stdout.log").write_text("")

        child_dir = jobs_dir / "j_token_task"
        child_dir.mkdir(parents=True)
        (child_dir / "task.json").write_text(json.dumps({
            "task_id": "1.1_tokens",
            "parent_job": "j_plan_auth",
        }))
        (child_dir / "state.json").write_text(json.dumps({
            "status": "completed",
            "duration_seconds": 10.0,
            "created_at": 1000,
        }))

        with patch("snodo.infrastructure.paths.require_project_root", return_value=tmp_dir):
            args = SimpleNamespace(composite_id="j_plan_auth", watch=False)
            res = logs_command(args)

            assert res == 0
            out = capsys.readouterr().out
            assert "Job j_plan_auth is a plan run" in out
            assert "Output is produced by child task jobs" in out
            assert "1.1_tokens" in out
            assert "j_token_task" in out
            # A completed child needs no look, so it grows no per-child hint —
            # but the generic placeholder must never appear either.
            assert "snodo logs <child_job_id>" not in out


def test_plan_job_logs_name_the_real_child_job_instead_of_a_placeholder(capsys):
    """A plan job's log output spells out the command with the real child id,
    never a generic placeholder the reader would have to resolve themselves."""
    import json
    import yaml

    with tempfile.TemporaryDirectory() as tmp_dir:
        snodo_dir = Path(tmp_dir) / ".snodo"
        jobs_dir = snodo_dir / "jobs"
        plans_dir = snodo_dir / "plans"

        plan_dir = plans_dir / "sweep"
        plan_dir.mkdir(parents=True)
        plan_yml = {
            "name": "sweep",
            "intent": "Sweep the contradiction",
            "waves": [
                {"id": 1, "depends_on": [],
                 "tasks": ["1.1_no-control", "1.2_models", "1.3_routes"]},
            ],
        }
        (plan_dir / "plan.yml").write_text(yaml.dump(plan_yml))
        (plan_dir / "status.json").write_text(json.dumps({
            "tasks": {
                "1.1_no-control": {"status": "blocked"},
                "1.2_models": {"status": "completed"},
                "1.3_routes": {"status": "completed"},
            }
        }))

        plan_job_dir = jobs_dir / "j_plan_sweep"
        plan_job_dir.mkdir(parents=True)
        (plan_job_dir / "task.json").write_text(json.dumps({"plan_name": "sweep"}))
        (plan_job_dir / "state.json").write_text(json.dumps({"status": "running", "job_type": "plan"}))
        (plan_job_dir / "stdout.log").write_text("")

        children = [
            ("j_blocked01", "1.1_no-control", "blocked"),
            ("j_models02", "1.2_models", "completed"),
            ("j_routes03", "1.3_routes", "completed"),
        ]
        for cid, ref, status in children:
            cdir = jobs_dir / cid
            cdir.mkdir(parents=True)
            (cdir / "task.json").write_text(json.dumps({
                "task_id": ref, "parent_job": "j_plan_sweep",
            }))
            (cdir / "state.json").write_text(json.dumps({
                "status": status, "duration_seconds": 3.0, "created_at": 1000,
            }))

        with patch("snodo.infrastructure.paths.require_project_root", return_value=tmp_dir):
            args = SimpleNamespace(composite_id="j_plan_sweep", watch=False)
            res = logs_command(args)

            assert res == 0
            out = capsys.readouterr().out
            assert "snodo logs j_blocked01" in out
            assert "snodo logs <child_job_id>" not in out
            # A wave with six tasks or one stays readable: only the row that
            # needs a look earns a hint; healthy children do not.
            assert "snodo logs j_models02" not in out
            assert "snodo logs j_routes03" not in out


def test_plan_job_watch_hint_points_at_the_command_that_follows(capsys):
    """Following a plan is one command that already exists; the non-watch view
    points at it rather than offering a second way to watch."""
    import json
    import yaml

    with tempfile.TemporaryDirectory() as tmp_dir:
        snodo_dir = Path(tmp_dir) / ".snodo"
        jobs_dir = snodo_dir / "jobs"
        plans_dir = snodo_dir / "plans"

        plan_dir = plans_dir / "sweep"
        plan_dir.mkdir(parents=True)
        (plan_dir / "plan.yml").write_text(yaml.dump({
            "name": "sweep", "intent": "Sweep",
            "waves": [{"id": 1, "depends_on": [], "tasks": ["1.1_x"]}],
        }))
        (plan_dir / "status.json").write_text(json.dumps({
            "tasks": {"1.1_x": {"status": "completed"}}
        }))

        plan_job_dir = jobs_dir / "j_plan_w"
        plan_job_dir.mkdir(parents=True)
        (plan_job_dir / "task.json").write_text(json.dumps({"plan_name": "sweep"}))
        (plan_job_dir / "state.json").write_text(json.dumps({"status": "completed", "job_type": "plan"}))
        (plan_job_dir / "stdout.log").write_text("")

        with patch("snodo.infrastructure.paths.require_project_root", return_value=tmp_dir):
            args = SimpleNamespace(composite_id="j_plan_w", watch=False)
            res = logs_command(args)

            assert res == 0
            out = capsys.readouterr().out
            assert "Follow this plan live: snodo logs j_plan_w --watch" in out


def test_plan_job_with_children_still_surfaces_them(capsys):
    """A plan that spawns a child task job per task keeps the #279 behaviour:
    the non-watch view names the children and does not print a summary only."""
    import json
    import yaml

    with tempfile.TemporaryDirectory() as tmp_dir:
        snodo_dir = Path(tmp_dir) / ".snodo"
        jobs_dir = snodo_dir / "jobs"
        plans_dir = snodo_dir / "plans"

        plan_dir = plans_dir / "spawns"
        plan_dir.mkdir(parents=True)
        (plan_dir / "plan.yml").write_text(yaml.dump({
            "name": "spawns", "intent": "Spawn two",
            "waves": [{"id": 1, "depends_on": [], "tasks": ["1.1_a", "1.2_b"]}],
        }))
        (plan_dir / "status.json").write_text(json.dumps({
            "tasks": {"1.1_a": {"status": "completed"}, "1.2_b": {"status": "running"}}
        }))

        plan_job_dir = jobs_dir / "j_plan_spawns"
        plan_job_dir.mkdir(parents=True)
        (plan_job_dir / "task.json").write_text(json.dumps({"plan_name": "spawns"}))
        (plan_job_dir / "state.json").write_text(json.dumps({"status": "running", "job_type": "plan"}))
        # A child-spawning plan writes nothing to its own stdout.
        (plan_job_dir / "stdout.log").write_text("")

        children = [
            ("j_childa", "1.1_a", "completed"),
            ("j_childb", "1.2_b", "running"),
        ]
        for cid, ref, status in children:
            cdir = jobs_dir / cid
            cdir.mkdir(parents=True)
            (cdir / "task.json").write_text(json.dumps({
                "task_id": ref, "parent_job": "j_plan_spawns",
            }))
            (cdir / "state.json").write_text(json.dumps({
                "status": status, "duration_seconds": 4.0, "created_at": 1000,
            }))

        with patch("snodo.infrastructure.paths.require_project_root", return_value=tmp_dir):
            args = SimpleNamespace(composite_id="j_plan_spawns", watch=False)
            res = logs_command(args)

            assert res == 0
            out = capsys.readouterr().out
            assert "Output is produced by child task jobs" in out
            assert "j_childa" in out
            assert "j_childb" in out
            # A running child earns a reachable hint with its real id (#279).
            assert "snodo logs j_childb" in out
            assert "(no stdout output)" not in out


def test_plan_job_with_own_output_streams_it(capsys):
    """A plan that ran its tasks in-process wrote the run to its own stdout.log;
    following it streams that output rather than printing only a summary."""
    import json
    import yaml

    with tempfile.TemporaryDirectory() as tmp_dir:
        snodo_dir = Path(tmp_dir) / ".snodo"
        jobs_dir = snodo_dir / "jobs"
        plans_dir = snodo_dir / "plans"

        plan_dir = plans_dir / "inproc"
        plan_dir.mkdir(parents=True)
        (plan_dir / "plan.yml").write_text(yaml.dump({
            "name": "inproc", "intent": "Run in-process",
            "waves": [{"id": 1, "depends_on": [], "tasks": ["1.1_solo"]}],
        }))
        (plan_dir / "status.json").write_text(json.dumps({
            "tasks": {"1.1_solo": {"status": "completed"}}
        }))

        plan_job_dir = jobs_dir / "j_plan_inproc"
        plan_job_dir.mkdir(parents=True)
        (plan_job_dir / "task.json").write_text(json.dumps({"plan_name": "inproc"}))
        (plan_job_dir / "state.json").write_text(json.dumps({"status": "completed", "job_type": "plan"}))
        # The whole run lives here — validator turns, tool calls, narration.
        (plan_job_dir / "stdout.log").write_text(
            "Wave 1:\n"
            "  [1.1_solo] executing...\n"
            "Turn 1: read_file\n"
            "Turn 2: (no tools called)\n"
            "  [1.1_solo] completed in 42.0s\n"
        )

        with patch("snodo.infrastructure.paths.require_project_root", return_value=tmp_dir):
            args = SimpleNamespace(composite_id="j_plan_inproc", watch=False)
            res = logs_command(args)

            assert res == 0
            out = capsys.readouterr().out
            # The summary stays as framing...
            assert "Wave 1:" in out
            assert "1.1_solo" in out
            # ...and the run's own output is what the reader can actually see.
            assert "Turn 1: read_file" in out
            assert "Turn 2: (no tools called)" in out
            assert "  [1.1_solo] executing..." in out
            assert "(no stdout output)" not in out


def test_plan_job_with_own_output_watch_streams_it(capsys):
    """Following (--watch) an in-process plan run streams its stdout.log even
    though it has no child jobs, instead of stopping at the summary."""
    import json
    import yaml

    with tempfile.TemporaryDirectory() as tmp_dir:
        snodo_dir = Path(tmp_dir) / ".snodo"
        jobs_dir = snodo_dir / "jobs"
        plans_dir = snodo_dir / "plans"

        plan_dir = plans_dir / "inproc_w"
        plan_dir.mkdir(parents=True)
        (plan_dir / "plan.yml").write_text(yaml.dump({
            "name": "inproc_w", "intent": "Run in-process and follow",
            "waves": [{"id": 1, "depends_on": [], "tasks": ["1.1_solo"]}],
        }))
        (plan_dir / "status.json").write_text(json.dumps({
            "tasks": {"1.1_solo": {"status": "completed"}}
        }))

        plan_job_dir = jobs_dir / "j_plan_inproc_w"
        plan_job_dir.mkdir(parents=True)
        (plan_job_dir / "task.json").write_text(json.dumps({"plan_name": "inproc_w"}))
        (plan_job_dir / "state.json").write_text(json.dumps({"status": "completed", "job_type": "plan"}))
        (plan_job_dir / "stdout.log").write_text(
            "intent body\n"
            "Turn 32: (no tools called)\n"
        )

        with patch("snodo.infrastructure.paths.require_project_root", return_value=tmp_dir):
            args = SimpleNamespace(composite_id="j_plan_inproc_w", watch=True)
            res = logs_command(args)

            assert res == 0
            out = capsys.readouterr().out
            assert "Following plan run j_plan_inproc_w" in out
            assert "Turn 32: (no tools called)" in out
            assert "Plan run j_plan_inproc_w finished (completed)" in out




def test_watch_stream_is_byte_identical_when_color_is_off(capsys, monkeypatch):
    """A piped / NO_COLOR watch prints exactly the log's bytes, in order (#294).

    The renderer must never alter what a non-interactive reader sees: every
    line the stdout.log holds is printed verbatim with its own newline. The
    capture stream here is not a tty, which is the real default for a pipe.
    """
    monkeypatch.delenv("NO_COLOR", raising=False)
    with tempfile.TemporaryDirectory() as tmp_dir:
        job_dir = Path(tmp_dir) / ".snodo" / "jobs" / "j_plain"
        job_dir.mkdir(parents=True)
        (job_dir / "stdout.log").write_text(
            "  Coder dispatched\n"
            "    [0:01] Turn 1: read_file(a.py)\n"
            "    [0:02] Turn 2: read_file(b.py)\n"
            "  Recovery stalled (attempt 2/3): halting loop\n"
        )

        with patch("snodo.infrastructure.paths.require_project_root", return_value=tmp_dir):
            mock_manager = MagicMock()
            mock_manager._job_dir.return_value = job_dir
            mock_manager.get_status.return_value = {"status": "completed"}
            with patch("snodo.jobs.JobManager", return_value=mock_manager):
                args = SimpleNamespace(composite_id="j_plain", watch=True)
                assert logs_command(args) == 0

        out = capsys.readouterr().out
        assert out == (
            "  Coder dispatched\n"
            "    [0:01] Turn 1: read_file(a.py)\n"
            "    [0:02] Turn 2: read_file(b.py)\n"
            "  Recovery stalled (attempt 2/3): halting loop\n"
        )
        assert "\x1b" not in out


def test_watch_stream_is_byte_identical_under_no_color(capsys, monkeypatch):
    """NO_COLOR produces today's plain lines even when stdout looks like a tty."""
    monkeypatch.setenv("NO_COLOR", "1")
    with tempfile.TemporaryDirectory() as tmp_dir:
        job_dir = Path(tmp_dir) / ".snodo" / "jobs" / "j_nc"
        job_dir.mkdir(parents=True)
        (job_dir / "stdout.log").write_text(
            "    [0:01] Turn 1: read_file(a.py)\n"
            "    [0:02] Turn 2: read_file(b.py)\n"
        )

        with patch("snodo.infrastructure.paths.require_project_root", return_value=tmp_dir):
            mock_manager = MagicMock()
            mock_manager._job_dir.return_value = job_dir
            mock_manager.get_status.return_value = {"status": "completed"}
            with patch("snodo.jobs.JobManager", return_value=mock_manager):
                args = SimpleNamespace(composite_id="j_nc", watch=True)
                assert logs_command(args) == 0

        out = capsys.readouterr().out
        assert out == (
            "    [0:01] Turn 1: read_file(a.py)\n"
            "    [0:02] Turn 2: read_file(b.py)\n"
        )
        assert "\x1b" not in out


def test_watch_stream_colors_and_compacts_when_color_is_on(capsys):
    """On an interactive terminal the watch styles lines and keeps a scrolling
    window of recent history rather than overwriting down to one row (#300):
    distinct turns and the halt that follows all stay visible together."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        job_dir = Path(tmp_dir) / ".snodo" / "jobs" / "j_color"
        job_dir.mkdir(parents=True)
        (job_dir / "stdout.log").write_text(
            "    [0:01] Turn 1: read_file(a.py)\n"
            "    [0:02] Turn 2: read_file(b.py)\n"
            "  Recovery stalled (attempt 2/3): halting loop\n"
        )

        with patch("snodo.infrastructure.paths.require_project_root", return_value=tmp_dir):
            mock_manager = MagicMock()
            mock_manager._job_dir.return_value = job_dir
            mock_manager.get_status.return_value = {"status": "completed"}
            with patch("snodo.jobs.JobManager", return_value=mock_manager), \
                 patch("snodo.engine.progress.color_enabled", return_value=True):
                args = SimpleNamespace(composite_id="j_color", watch=True)
                assert logs_command(args) == 0

        out = capsys.readouterr().out
        assert "\x1b" in out
        stripped = _strip_ansi(out)
        # Neither turn overwrote the other, and the halt survived both.
        assert "Turn 1: read_file(a.py)" in stripped
        assert "Turn 2: read_file(b.py)" in stripped
        assert "Recovery stalled" in stripped


def _strip_ansi(text: str) -> str:
    import re
    return re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)


def _plan_watch_tree(tmp_dir: str) -> None:
    """A plan run 'wave' with one task '1.1_a', running on disk.

    Only the files the watch reads directly — the plan dir, the plan job's
    stdout.log, the task-status file — are created here; the child job rows
    come from a patched JobManager so each test controls their statuses and
    durations without the process-reconcile that real running rows undergo.
    """
    import json
    import yaml

    snodo_dir = Path(tmp_dir) / ".snodo"
    plan_dir = snodo_dir / "plans" / "wave"
    plan_dir.mkdir(parents=True)
    (plan_dir / "plan.yml").write_text(yaml.dump({
        "name": "wave", "intent": "A long wave",
        "waves": [{"id": 1, "depends_on": [], "tasks": ["1.1_a"]}],
    }))
    (plan_dir / "status.json").write_text(json.dumps({
        "tasks": {"1.1_a": {"status": "running"}}
    }))

    plan_job_dir = snodo_dir / "jobs" / "j_plan_hb"
    plan_job_dir.mkdir(parents=True)
    (plan_job_dir / "task.json").write_text(json.dumps({"plan_name": "wave"}))
    (plan_job_dir / "state.json").write_text(json.dumps({"status": "running", "job_type": "plan"}))
    (plan_job_dir / "stdout.log").write_text("")


def _running_child_rows(duration: float) -> list[dict]:
    return [
        {
            "id": "j_plan_hb", "status": "running", "plan": "wave",
            "parent_job": "", "task_ref": "", "created_at": 900,
            "duration_seconds": None,
        },
        {
            "id": "j_child_hb", "status": "running", "plan": "",
            "parent_job": "j_plan_hb", "task_ref": "1.1_a",
            "created_at": 950, "duration_seconds": duration,
        },
    ]


def _install_fake_clock(monkeypatch, start: float = 1000.0, stop_after: int = 12):
    """Give logs_cmd a clock where one sleep advances time by exactly one second.

    Replaces the whole ``time`` module on the command's namespace, so only the
    watch is on fake time and the test's own timing stays real. Raises
    KeyboardInterrupt out of the loop after *stop_after* polls.
    """
    from snodo.cli.commands import logs_cmd

    clock = {"now": start, "polls": 0}

    def fake_sleep(_secs):
        clock["polls"] += 1
        clock["now"] += 1.0
        if clock["polls"] >= stop_after:
            raise KeyboardInterrupt

    monkeypatch.setattr(
        logs_cmd, "time",
        SimpleNamespace(time=lambda: clock["now"], sleep=fake_sleep),
    )
    return clock


def test_watch_updates_elapsed_with_no_status_change(capsys, monkeypatch):
    """A tty plan watch keeps the elapsed time of what is running current between
    transitions (#316): the healthy nothing-to-report wave that used to freeze
    the screen now redraws one in-place line whose numbers move.
    """
    clock = _install_fake_clock(monkeypatch)

    with tempfile.TemporaryDirectory() as tmp_dir:
        _plan_watch_tree(tmp_dir)
        with patch("snodo.infrastructure.paths.require_project_root", return_value=tmp_dir):
            from snodo.jobs import JobManager

            def get_status(jid):
                return {
                    "status": "running",
                    "job_type": "plan",
                    "started_at": 900.0,
                    "task": {"plan_name": "wave"},
                }

            def list_jobs():
                # The child holds one status across every poll; only its
                # running time moves, exactly as a healthy coder's does.
                return _running_child_rows(round(clock["now"] - 950.0, 1))

            with patch.object(JobManager, "get_status", side_effect=get_status), \
                 patch.object(JobManager, "list_jobs", side_effect=list_jobs), \
                 patch("snodo.engine.progress.color_enabled", return_value=True):
                args = SimpleNamespace(composite_id="j_plan_hb", watch=True)
                assert logs_command(args) == 0

    out = capsys.readouterr().out
    # The liveness line: drawn in place (a cursor move is present), naming
    # the elapsed time of the run and of what is still running.
    assert "\x1b[K" in out, out
    heartbeats = [ln for ln in _strip_ansi(out).splitlines() if "no status change" in ln]
    assert len(heartbeats) >= 2, heartbeats
    # It keeps the elapsed time *current*: plan 1:45 at the first draw grew to
    # 1:50 at the next, and the child crossed from seconds into minutes.
    assert any("plan 1:45" in ln for ln in heartbeats), heartbeats
    assert any("plan 1:50" in ln for ln in heartbeats), heartbeats
    assert any("1:00" in ln for ln in heartbeats), heartbeats
    # Alive without being a busy repaint: the status block is not reprinted,
    # only the one heartbeat line moves.
    assert out.count("Following plan run j_plan_hb") == 1
    assert "Intent: A long wave" in _strip_ansi(out)


def test_watch_non_tty_never_repaints(capsys, monkeypatch):
    """A piped / redirected plan watch stays today's plain append-only lines
    (#316): no cursor movement, no repeated blocks, nothing to flood the pipe.
    """
    clock = _install_fake_clock(monkeypatch)

    with tempfile.TemporaryDirectory() as tmp_dir:
        _plan_watch_tree(tmp_dir)
        with patch("snodo.infrastructure.paths.require_project_root", return_value=tmp_dir):
            from snodo.jobs import JobManager

            def get_status(jid):
                return {
                    "status": "running",
                    "job_type": "plan",
                    "started_at": 900.0,
                    "task": {"plan_name": "wave"},
                }

            def list_jobs():
                return _running_child_rows(round(clock["now"] - 950.0, 1))

            # No colour patch at all: a non-tty stdout yields renderer None,
            # which is the real condition behind a pipe or a redirect.
            with patch.object(JobManager, "get_status", side_effect=get_status), \
                 patch.object(JobManager, "list_jobs", side_effect=list_jobs):
                args = SimpleNamespace(composite_id="j_plan_hb", watch=True)
                assert logs_command(args) == 0

    out = capsys.readouterr().out
    assert "\x1b" not in out, out
    assert "no status change" not in out, out
    # Exactly what it emits today: the opening snapshot, and nothing further
    # while no status changes.
    assert out.count("Following plan run j_plan_hb") == 1
    assert out.count("1.1_a") == 1
    assert "1.1_a: running" in out


def test_watch_duration_crosses_units(capsys, monkeypatch):
    """A duration shown by the watch reads in its larger unit as it grows (#316):
    1355.5 seconds is "22:35", and one under a minute still reads as seconds.
    """
    _install_fake_clock(monkeypatch, stop_after=2)
    polls = {"n": 0}

    with tempfile.TemporaryDirectory() as tmp_dir:
        _plan_watch_tree(tmp_dir)
        with patch("snodo.infrastructure.paths.require_project_root", return_value=tmp_dir):
            from snodo.jobs import JobManager

            def get_status(jid):
                if polls["n"] >= 1:
                    return {
                        "status": "completed", "exit_code": 0,
                        "job_type": "plan", "task": {"plan_name": "wave"},
                    }
                return {
                    "status": "running", "job_type": "plan",
                    "task": {"plan_name": "wave"},
                }

            def list_jobs():
                rows = _running_child_rows(7.5)
                if polls["n"] >= 1:
                    # The child lands with 22m35s of work.
                    rows[1]["status"] = "completed"
                    rows[1]["duration_seconds"] = 1355.5
                polls["n"] += 1
                return rows

            with patch.object(JobManager, "get_status", side_effect=get_status), \
                 patch.object(JobManager, "list_jobs", side_effect=list_jobs):
                args = SimpleNamespace(composite_id="j_plan_hb", watch=True)
                assert logs_command(args) == 0

    out = capsys.readouterr().out
    # Minutes once past a minute, still plain seconds under one.
    assert "in 22:35" in out, out
    assert "in 7.5s" in out, out
    assert "1355.5s" not in out, out

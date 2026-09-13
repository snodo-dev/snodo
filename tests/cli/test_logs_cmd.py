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

"""Tests for in-place coder attribution records (Fixes #69).

PROVES:
- Completed in-place coder runs (agy, opencode-cli, opencode) leave an attribution
  record in state.json under "usage".
- The attribution record is explicitly distinguishable from a zero-cost run:
  cost is None (not 0.0), tokens are None (not 0), source is "inplace_coder",
  and the "measured" list declares exactly what was directly observed (elapsed_ms, model).
- The litellm path's existing token and cost accounting is pinned and unaffected.
"""

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
import pytest

from snodo.coders.agy_adapter import AGYAdapter
from snodo.coders.opencode_cli_adapter import OpenCodeCLIAdapter
from snodo.core.interfaces import TaskSpec
from snodo.infrastructure.usage_tracker import UsageTracker, record_inplace_coder_run


@pytest.fixture
def temp_workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    (root / "README.md").write_text("# Test Workspace\n")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=root, check=True)
    return root


def _setup_job_dir(project_root: Path, job_id: str) -> Path:
    job_dir = project_root / ".snodo" / "jobs" / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "state.json").write_text(json.dumps({"job_id": job_id, "usage": []}))
    return job_dir


def _setup_task_dir(project_root: Path, task_id: str) -> Path:
    task_dir = project_root / ".snodo" / "tasks" / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "state.json").write_text(json.dumps({"task_id": task_id, "usage": []}))
    return task_dir


class TestInPlaceCoderAttribution:
    def test_agy_run_leaves_attribution_record_in_job_state(self, temp_workspace: Path, monkeypatch):
        """In-place AGY run records wall-clock duration and model in job state.json."""
        project_root = temp_workspace.parent
        job_id = "j_inplace_agy_01"
        _setup_job_dir(project_root, job_id)

        monkeypatch.setenv("SNODO_PROJECT_ROOT", str(project_root))
        monkeypatch.setenv("SNODO_JOB_ID", job_id)

        adapter = AGYAdapter(model="agy/gemini-3.7-flash", workspace=temp_workspace)
        spec = TaskSpec(description="Implement feature Y", constraints=[])

        def fake_run(argv, project_root, **kwargs):
            # Simulate the host CLI running and writing files in place.
            (temp_workspace / "feature_y.py").write_text("def y(): return 42\n")
            return subprocess.CompletedProcess(
                args=argv, returncode=0, stdout="Success", stderr="",
            )

        # Patch the adapter's own subprocess call (the seam sibling coder
        # tests use), NOT subprocess.Popen: a module-global patch would
        # capture every other caller in the process. GitPython runs
        # "git version" through Popen during its own initialisation, and the
        # adapter imports git lazily inside implement() — a global patch
        # breaks that import.
        with mock.patch.object(adapter, "_run_subprocess", side_effect=fake_run):
            artifact = adapter.implement(spec)

        # Artifact metadata includes measured metrics
        assert artifact.metadata["coder"] == "agy"
        assert artifact.metadata["model"] == "agy/gemini-3.7-flash"
        assert artifact.metadata["duration_ms"] >= 0.0

        # Persisted state.json usage contains the attribution record
        state_path = project_root / ".snodo" / "jobs" / job_id / "state.json"
        state = json.loads(state_path.read_text())
        usage = state.get("usage", [])
        assert len(usage) == 1

        record = usage[0]
        # Distinguishable from a zero-cost run:
        assert record["source"] == "inplace_coder"
        assert record["coder"] == "agy"
        assert record["model"] == "agy/gemini-3.7-flash"
        assert record["role"] == "coder"
        assert isinstance(record["duration_ms"], float)
        assert record["duration_ms"] >= 0.0
        # Cost and tokens are explicitly None (unmeasured), NOT 0.0 or 0
        assert record["cost"] is None
        assert record["prompt_tokens"] is None
        assert record["completion_tokens"] is None
        assert record["total_tokens"] is None
        # Explicit declaration of what was directly observed
        assert "duration_ms" in record["measured"]
        assert "model" in record["measured"]
        # Duplicate elapsed_ms field must not exist
        assert "elapsed_ms" not in record

    def test_opencode_cli_run_leaves_attribution_record(self, temp_workspace: Path, monkeypatch):
        """In-place OpenCode CLI run records coder_name 'opencode-cli'."""
        project_root = temp_workspace.parent
        task_id = "task_inplace_opencode_01"
        _setup_task_dir(project_root, task_id)

        monkeypatch.setenv("SNODO_PROJECT_ROOT", str(project_root))

        adapter = OpenCodeCLIAdapter(model="opencode-cli/claude-3-5-sonnet", workspace=temp_workspace)
        spec = TaskSpec(
            description="Implement feature Z",
            constraints=[],
            project_context={"task_id": task_id},
        )

        def fake_run(argv, project_root, **kwargs):
            (temp_workspace / "feature_z.py").write_text("def z(): pass\n")
            return subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")

        with mock.patch.object(adapter, "_run_subprocess", side_effect=fake_run):
            adapter.implement(spec)

        state_path = project_root / ".snodo" / "tasks" / task_id / "state.json"
        state = json.loads(state_path.read_text())
        usage = state.get("usage", [])
        assert len(usage) == 1

        record = usage[0]
        assert record["source"] == "inplace_coder"
        assert record["coder"] == "opencode-cli"
        assert record["model"] == "opencode-cli/claude-3-5-sonnet"
        assert record["cost"] is None
        assert "duration_ms" in record["measured"]

    def test_missing_coder_name_is_caught_by_conformance_test_not_run_time(self, temp_workspace: Path):
        """A subclass that forgets coder_name does not crash a committed run;
        the conformance test catches the omission at test time (Refs #206)."""
        from snodo.coders.base import InPlaceCoderAdapter
        from snodo.coders import CODER_REGISTRY
        from snodo.core.interfaces import CodeArtifact
        from tests.coders.test_adapter_conformance import (
            test_declared_capabilities_are_present_on_every_adapter,
        )

        class _UndeclaredCoder(InPlaceCoderAdapter):
            # Deliberately omits coder_name — the mistake the check guards.
            _workspace = temp_workspace

            def __init__(self, **kwargs):
                pass

            def _implement_in_place(self, spec):
                (temp_workspace / "feature_a.py").write_text("def a(): pass\n")
                return CodeArtifact(files=[])

        # Run time: attribution is best-effort and must never break the run,
        # even though the coder already committed. implement() returns cleanly.
        (temp_workspace / "README.md").write_text("# touched\n")
        adapter = _UndeclaredCoder()
        artifact = adapter.implement(TaskSpec(description="t", constraints=[]))
        assert artifact is not None
        assert "coder" not in artifact.metadata
        # The committed run survived: the change is on HEAD, not rolled back.
        import subprocess
        head = subprocess.run(
            ["git", "show", "--stat", "--oneline"], cwd=temp_workspace,
            capture_output=True, text=True,
        ).stdout
        assert "feature_a.py" in head

        # Test time: registering the same omission is caught by the conformance
        # gate rather than surfacing at run time.
        CODER_REGISTRY["_undeclared_coder"] = _UndeclaredCoder
        try:
            with pytest.raises(AssertionError, match="coder_name"):
                test_declared_capabilities_are_present_on_every_adapter("_undeclared_coder")
        finally:
            del CODER_REGISTRY["_undeclared_coder"]

    def test_record_inplace_coder_run_direct(self, tmp_path: Path, monkeypatch):
        """Direct call to record_inplace_coder_run safely persists records."""
        project_root = tmp_path
        job_id = "j_direct_01"
        task_id = "task_direct_01"
        _setup_job_dir(project_root, job_id)
        _setup_task_dir(project_root, task_id)

        monkeypatch.setenv("SNODO_PROJECT_ROOT", str(project_root))

        record_inplace_coder_run(
            coder="custom-coder",
            model="",
            duration_ms=123.45,
            job_id=job_id,
            task_id=task_id,
        )

        job_state = json.loads((project_root / ".snodo" / "jobs" / job_id / "state.json").read_text())
        assert len(job_state["usage"]) == 1
        rec = job_state["usage"][0]
        assert rec["coder"] == "custom-coder"
        assert rec["model"] == ""
        assert rec["measured"] == ["duration_ms"]  # model omitted when empty
        assert rec["duration_ms"] == 123.45
        assert rec["cost"] is None
        assert "elapsed_ms" not in rec



class TestLiteLLMAccountingPinned:
    """Pin the litellm path's existing accounting to ensure no regressions."""

    def test_litellm_success_callback_records_tokens_and_cost(self, tmp_path: Path, monkeypatch):
        project_root = tmp_path
        job_id = "j_litellm_pin_01"
        _setup_job_dir(project_root, job_id)

        monkeypatch.setenv("SNODO_PROJECT_ROOT", str(project_root))
        monkeypatch.setenv("SNODO_JOB_ID", job_id)

        tracker = UsageTracker()

        response_obj = SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=150, completion_tokens=50),
        )

        kwargs = {
            "model": "gpt-4o",
            "metadata": {"role": "coder", "job_id": job_id},
        }

        # Mock completion cost
        with mock.patch("litellm.completion_cost", return_value=0.0035):
            tracker.log_success_event(
                kwargs=kwargs,
                response_obj=response_obj,
                start_time=100.0,
                end_time=102.5,
            )

        job_state = json.loads((project_root / ".snodo" / "jobs" / job_id / "state.json").read_text())
        usage = job_state.get("usage", [])
        assert len(usage) == 1

        rec = usage[0]
        # Litellm path produces measured numbers, NOT None
        assert rec["model"] == "gpt-4o"
        assert rec["prompt_tokens"] == 150
        assert rec["completion_tokens"] == 50
        assert rec["total_tokens"] == 200
        assert rec["cost"] == 0.0035
        assert rec["duration_ms"] == 2500.0
        assert rec["role"] == "coder"
        # Source is NOT inplace_coder
        assert rec.get("source") != "inplace_coder"


# ============================================================================
# Guards on the tests themselves
#
# The two adapter tests patch subprocess.Popen to fake the host CLI. That
# patch must reach NO further than the adapter's own call site: GitPython
# runs "git version" through Popen during its own import, and the adapter
# imports git lazily inside implement(). A module-global subprocess.Popen
# patch breaks that import (see the commit message for #243).
# ============================================================================


class TestAttributionGuard:
    def test_tests_fail_if_adapter_stops_recording_attribution(self, tmp_path: Path):
        """If the adapter stops writing the attribution record, the adapter
        tests in this file must fail — a regression in the recording path
        must be a red suite, not a silent pass.

        Drives the two adapter tests directly (same assertions, same code)
        with ``record_inplace_coder_run`` patched to a no-op and asserts each
        FAILS. Their assertions read the persisted state.json, so a pass under
        a no-op recorder would mean the assertions stopped checking the record.
        """
        from _pytest.monkeypatch import MonkeyPatch

        recorder = TestInPlaceCoderAttribution()

        mp = MonkeyPatch()
        try:
            mp.setattr(
                "snodo.infrastructure.usage_tracker.record_inplace_coder_run",
                lambda **_kw: None,
            )
            for test_fn in (
                recorder.test_agy_run_leaves_attribution_record_in_job_state,
                recorder.test_opencode_cli_run_leaves_attribution_record,
            ):
                workspace = tmp_path / f"ws_{test_fn.__name__}"
                workspace.mkdir()
                subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
                subprocess.run(["git", "config", "user.email", "t@t"], cwd=workspace, check=True)
                subprocess.run(["git", "config", "user.name", "T"], cwd=workspace, check=True)
                (workspace / "README.md").write_text("# probe\n")
                subprocess.run(["git", "add", "."], cwd=workspace, check=True)
                subprocess.run(["git", "commit", "-qm", "init"], cwd=workspace, check=True)
                with pytest.raises(AssertionError):
                    test_fn(workspace, mp)
        finally:
            mp.undo()

    def test_no_test_in_this_file_patches_module_global_libraries(self):
        """No test in this file patches a module-global another library relies
        on. The fix scopes the fake of the adapter's subprocess call to the
        adapter instance (``_run_subprocess``); this guard asserts the tests
        never reach for ``subprocess.Popen`` directly."""
        src = Path(__file__).read_text()
        # Scan only the actual test bodies, not this guard's own literals.
        test_class = src.index("class TestInPlaceCoderAttribution")
        guard_comment = src.index("# Guards on the tests themselves")
        body = src[test_class:guard_comment]
        forbidden = (
            'mock.patch("subprocess.Popen"',
            "mock.patch('subprocess.Popen'",
        )
        for line in body.splitlines():
            assert not any(frag in line for frag in forbidden), line


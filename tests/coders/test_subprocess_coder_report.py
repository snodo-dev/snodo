"""A subprocess coder is invited to report, and says nothing if it will not.

FILE: tests/coders/test_subprocess_coder_report.py

The engine cannot count a subprocess coder's turns and must not read its prose,
so it offers the coder a path inside the JOB's own state and asks it to write an
ADR 048 report there before exiting (Fixes #318). These tests pin the four
properties the invitation's contract is built on:

  * a coder that writes a full report has it read back onto the adapter;
  * a coder that writes nothing produces a run identical to today's — no
    report, no warning, no change to the artifact;
  * a malformed report is discarded with a log line and the run is unaffected;
  * the report file never survives the run, and it never lands inside the
    user's project (workspace) — only under ``.snodo/``.

The fake "CLI" is a real subprocess: the prompt the engine builds is delivered
to it, so these tests also prove the invitation names a usable path and does not
replace what the coder was asked to build.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from snodo.coders.subprocess_adapter import SubprocessCoderAdapter
from snodo.core.interfaces import TaskSpec

_REPORT_FILENAME = "coder-report.json"

_FAKE_CLI = r'''
import json
import pathlib
import re
import sys

mode = sys.argv[1]
prompt = sys.argv[2]

match = re.search(r"to:\n\s+(\S*coder-report\.json)", prompt)
report_path = pathlib.Path(match.group(1)) if match else None

# The coder actually does the task: write a file in the granted workspace.
pathlib.Path("feature.py").write_text("def feature():\n    return 1\n")

if mode == "none":
    sys.exit(0)

report = {
    "files": [{"path": "feature.py", "kind": "created"}],
    "turns_used": 3,
    "turns_available": 10,
    "tokens_used": 1200,
    "context_window": 200000,
    "wall_time_ms": 4500,
    "stop_reason": "completed",
}

if mode == "malformed":
    report_path.write_text("{not valid json at all")
else:
    report_path.write_text(json.dumps(report))
'''


class _ReportingAdapter(SubprocessCoderAdapter):
    """Subprocess adapter whose 'CLI' runs a script that may write a report."""

    coder_name: str = "reporting-test"
    binary: str = "reporting-test"
    model_prefix: str = "reporting-test/"
    install_hint: str = "n/a — test adapter"

    def __init__(self, script: Path, mode: str, **kwargs):
        super().__init__(**kwargs)
        self._script = script
        self._mode = mode

    def _build_argv(self, prompt: str, project_root: str, model: str) -> list[str]:
        return [sys.executable, "-u", str(self._script), self._mode, prompt]


def _make_workspace(base: Path, name: str) -> Path:
    """A real git repo the coder writes in — the granted containment boundary."""
    ws = base / name
    ws.mkdir()
    for cmd in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "test@test.com"],
        ["git", "config", "user.name", "Test"],
    ):
        subprocess.run(cmd, cwd=ws, check=True)
    (ws / "README.md").write_text("# Test Workspace\n")
    subprocess.run(["git", "add", "."], cwd=ws, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=ws, check=True)
    return ws


def _adapter(
    tmp_path: Path,
    workspace: Path,
    mode: str,
    monkeypatch,
    *,
    project: Path | None = None,
) -> _ReportingAdapter:
    """Build the fake-CLI adapter, optionally pointing it at a job's state."""
    script = tmp_path / "fake_cli.py"
    if not script.exists():
        script.write_text(_FAKE_CLI)
    if project is not None:
        monkeypatch.setenv("SNODO_PROJECT_ROOT", str(project))
        monkeypatch.setenv("SNODO_JOB_ID", "j_report01")
    else:
        monkeypatch.delenv("SNODO_PROJECT_ROOT", raising=False)
        monkeypatch.delenv("SNODO_JOB_ID", raising=False)
    adapter = _ReportingAdapter(script=script, mode=mode, workspace=workspace)
    adapter._job_id = "j_report01"
    adapter._task_id = "task_report01"
    return adapter


def _report_path(project: Path) -> Path:
    return project / ".snodo" / "jobs" / "j_report01" / _REPORT_FILENAME


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A project root carrying the job's own state directory."""
    root = tmp_path / "project"
    job_dir = root / ".snodo" / "jobs" / "j_report01"
    job_dir.mkdir(parents=True)
    (job_dir / "state.json").write_text("{}")
    return root


def test_a_full_report_is_read_back(tmp_path, project, monkeypatch):
    workspace = _make_workspace(tmp_path, "ws_full")
    adapter = _adapter(tmp_path, workspace, "full", monkeypatch, project=project)

    artifact = adapter.implement(TaskSpec(description="build a feature", constraints=[]))

    assert adapter.last_report is not None
    assert adapter.last_report.stop_reason == "completed"
    assert adapter.last_report.turns_used == 3
    assert adapter.last_report.turns_available == 10
    assert adapter.last_report.tokens_used == 1200
    assert adapter.last_report.context_window == 200000
    assert adapter.last_report.wall_time_ms == 4500
    assert [(f.path, f.kind) for f in adapter.last_report.files] == [
        ("feature.py", "created"),
    ]
    # The report is evidence, never a verdict: it changes nothing about the run.
    assert "feature.py" in [f.path for f in artifact.files]


def test_nothing_written_produces_a_run_identical_to_today(
    tmp_path, project, monkeypatch, caplog
):
    invited_ws = _make_workspace(tmp_path, "ws_invited")
    invited = _adapter(tmp_path, invited_ws, "none", monkeypatch, project=project)

    with caplog.at_level("WARNING"):
        artifact = invited.implement(TaskSpec(description="build a feature", constraints=[]))

    # No report, and no complaint about one: a missing report is the ordinary
    # case, never an error (ADR 048).
    assert invited.last_report is None
    assert not [r for r in caplog.records if r.levelno >= 30]

    # The artifact is exactly what the same coder produces with no invitation.
    # A separate worktree, because the first run committed its file.
    bare_ws = _make_workspace(tmp_path, "ws_uninvited")
    uninvited = _adapter(tmp_path, bare_ws, "none", monkeypatch)

    plain = uninvited.implement(TaskSpec(description="build a feature", constraints=[]))

    assert plain.files == artifact.files
    assert uninvited.last_report is None


def test_a_malformed_report_is_discarded_without_affecting_the_run(
    tmp_path, project, monkeypatch, caplog
):
    workspace = _make_workspace(tmp_path, "ws_bad")
    adapter = _adapter(tmp_path, workspace, "malformed", monkeypatch, project=project)

    with caplog.at_level("WARNING", logger="snodo.coders.report"):
        artifact = adapter.implement(TaskSpec(description="build a feature", constraints=[]))

    assert adapter.last_report is None
    assert any(r.levelno >= 30 for r in caplog.records), (
        "a discarded report must leave a log line, not pass silently"
    )
    # The worktree's work still travels: the run is judged on what it wrote.
    assert "feature.py" in [f.path for f in artifact.files]


def test_no_report_file_is_left_behind(tmp_path, project, monkeypatch):
    for mode in ("full", "none", "malformed"):
        workspace = _make_workspace(tmp_path, f"ws_left_{mode}")
        adapter = _adapter(tmp_path, workspace, mode, monkeypatch, project=project)
        adapter.implement(TaskSpec(description="build a feature", constraints=[]))
        assert not _report_path(project).exists(), (
            f"the report file survived a {mode!r} run"
        )
        # Never inside the user's project: the workspace holds no report.
        assert not (workspace / _REPORT_FILENAME).exists()


def test_report_written_into_the_workspace_snodo_is_cleaned_up_not_flagged(
    tmp_path, monkeypatch
):
    """A degraded run has no separate worktree: the job state is the workspace.

    The report then lands under the workspace's own ``.snodo/``, which the
    in-place mutation guard watches. Reading and deleting it before the guard
    compares snapshots keeps the net change zero, so an invited report is never
    mistaken for a governance violation (Fixes #318).
    """
    project = _make_workspace(tmp_path, "ws_degraded")
    (project / ".snodo" / "jobs" / "j_report01").mkdir(parents=True)
    (project / ".snodo" / "jobs" / "j_report01" / "state.json").write_text("{}")

    adapter = _adapter(tmp_path, project, "full", monkeypatch, project=project)

    artifact = adapter.implement(TaskSpec(description="build a feature", constraints=[]))

    assert adapter.last_report is not None
    assert adapter.last_report.stop_reason == "completed"
    assert "feature.py" in [f.path for f in artifact.files]
    assert not _report_path(project).exists()


def test_the_invitation_names_a_job_state_path_not_the_workspace(
    tmp_path, project, monkeypatch
):
    workspace = _make_workspace(tmp_path, "ws_invite")
    adapter = _adapter(tmp_path, workspace, "none", monkeypatch, project=project)
    prompt = adapter._build_prompt(TaskSpec(description="build a feature", constraints=[]))

    invited = adapter._invite_report(prompt)

    report_path = str(_report_path(project))
    assert report_path in invited
    assert str(workspace / _REPORT_FILENAME) not in invited
    # The instruction is additive: the task is still what it always was.
    assert prompt in invited
    assert "build a feature" in invited


def test_no_job_state_means_no_invitation_and_todays_prompt(
    tmp_path, project, monkeypatch
):
    workspace = _make_workspace(tmp_path, "ws_nojob")
    adapter = _adapter(tmp_path, workspace, "none", monkeypatch)

    prompt = adapter._build_prompt(TaskSpec(description="build a feature", constraints=[]))

    assert adapter._report_path() is None
    assert adapter._invite_report(prompt) == prompt

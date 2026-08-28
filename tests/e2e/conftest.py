"""Shared fixtures for E2E CLI tests.

FILE: tests/e2e/conftest.py (Task 7.13)

Provides isolated test environments for subprocess-based CLI testing.
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import List

import pytest


def pytest_collection_modifyitems(config, items):
    """Fail collection if a test under tests/e2e/ lacks the ``e2e`` marker.

    The default addopts (``-m 'not e2e'``) excludes marked tests from the
    fast pass, so an unmarked test that lives in tests/e2e/ runs in every
    ``pytest tests/`` invocation. That happened once (test_init_project_id.py,
    ~10-40s of suite time); this makes it a collection error instead of a
    silent recurrence.
    """
    e2e_dir = Path(config.rootdir) / "tests" / "e2e"
    for item in items:
        if e2e_dir not in item.path.parents:
            continue
        if item.get_closest_marker("e2e") is not None:
            continue
        raise pytest.UsageError(
            f"{item.nodeid} lives in tests/e2e/ but does not carry the e2e "
            "marker, so the default addopts (-m 'not e2e') does not exclude it "
            "from the fast pass. Add `pytestmark = pytest.mark.e2e` to the "
            "module (or the marker on the individual test)."
        )


def _snodo_cmd() -> List[str]:
    return [sys.executable, "-m", "snodo"]


@pytest.fixture
def snodo_cli(tmp_path):
    """Fixture returning a callable that runs snodo as a subprocess.

    Each invocation gets:
        - a nested project root under the pytest tmpdir
        - SNODO_HOME pointing to a separate tmpdir for that test
        - SNODO_TOKEN_SECRET set to a fixed deterministic value
        - stdout/stderr captured as text
        - a git repository initialized in the nested project root

    The project is nested below *tmp_path* because `snodo run` places task
    worktrees in `<project_root>/../.snodo-worktrees`. Nesting keeps those
    siblings per-test under parallel pytest runs.
    """
    project_root = tmp_path / "project"
    snodo_home = tmp_path / "snodo_home"
    project_root.mkdir()
    snodo_home.mkdir()

    # Initialize git repo (snodo requires one)
    subprocess.run(["git", "init", "-q"], cwd=str(project_root), check=False)
    subprocess.run(
        ["git", "config", "user.email", "test@e2e.local"],
        cwd=str(project_root), check=False,
    )
    subprocess.run(
        ["git", "config", "user.name", "E2E Test"],
        cwd=str(project_root), check=False,
    )

    def _run(cmd_args: List[str], **kwargs) -> subprocess.CompletedProcess:
        env = os.environ.copy()
        env["SNODO_HOME"] = str(snodo_home)
        env["SNODO_TOKEN_SECRET"] = "e2e_test_fixed_secret_32bytes!"
        env["PYTHONIOENCODING"] = "utf-8"
        # The audit log is a property of the PROJECT (Fixes #111): the CLI must
        # write to <project_root>/.snodo/audit.log, not to SNODO_HOME. The
        # in-process suite fixture sets SNODO_AUDIT_LOG to keep unit tests off
        # the suite repo; a subprocess must NOT inherit it, or it would write
        # the project's audit log to the test harness's temp file instead.
        env.pop("SNODO_AUDIT_LOG", None)
        return subprocess.run(
            _snodo_cmd() + cmd_args,
            cwd=str(project_root),
            env=env,
            capture_output=True,
            text=True,
            **kwargs,
        )
    _run.home = project_root
    _run.snodo_home = snodo_home
    return _run


@pytest.fixture
def initialized_project(snodo_cli):
    """Return a tmp_path with snodo init already run (team template by default)."""
    def _init(template: str = "team") -> Path:
        result = snodo_cli(["init", "--template", template, "--force", "--yes"])
        assert result.returncode == 0, f"init failed: {result.stderr}"
        return snodo_cli.home
    return _init


@pytest.fixture
def audit_log_entries(snodo_cli):
    """Parse JSONL audit log from project .snodo/audit.log.

    A missing log and an empty log are different facts: a missing file means
    the CLI never wrote the project audit log (a resolution bug), while an
    empty file means the run produced no events. Missing raises so the caller
    cannot mistake "no log written" for "no events".
    """
    def _load() -> List[dict]:
        audit_path = snodo_cli.home / ".snodo" / "audit.log"
        if not audit_path.exists():
            raise FileNotFoundError(
                f"audit log not found at {audit_path} — the CLI did not write "
                "the project audit log. The audit log must resolve against the "
                "project root, not SNODO_HOME (Fixes #111)."
            )
        entries = []
        for line in audit_path.read_text().splitlines():
            if line.strip():
                entries.append(json.loads(line))
        return entries
    return _load

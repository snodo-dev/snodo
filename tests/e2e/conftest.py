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
        - SNODO_HOME pointing to an isolated tmpdir
        - SNODO_TOKEN_SECRET set to a fixed deterministic value
        - stdout/stderr captured as text
        - A git repository initialized in the working directory
    """
    # Initialize git repo (snodo requires one)
    subprocess.run(["git", "init", "-q"], cwd=str(tmp_path), check=False)
    subprocess.run(
        ["git", "config", "user.email", "test@e2e.local"],
        cwd=str(tmp_path), check=False,
    )
    subprocess.run(
        ["git", "config", "user.name", "E2E Test"],
        cwd=str(tmp_path), check=False,
    )

    def _run(cmd_args: List[str], **kwargs) -> subprocess.CompletedProcess:
        home = tmp_path / "snodo_home"
        home.mkdir(exist_ok=True)
        env = os.environ.copy()
        env["SNODO_HOME"] = str(home)
        env["SNODO_TOKEN_SECRET"] = "e2e_test_fixed_secret_32bytes!"
        env["PYTHONIOENCODING"] = "utf-8"
        return subprocess.run(
            _snodo_cmd() + cmd_args,
            cwd=str(tmp_path),
            env=env,
            capture_output=True,
            text=True,
            **kwargs,
        )
    _run.home = tmp_path
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
    """Parse JSONL audit log from project .snodo/audit.log."""
    def _load() -> List[dict]:
        audit_path = snodo_cli.home / ".snodo" / "audit.log"
        if not audit_path.exists():
            return []
        entries = []
        for line in audit_path.read_text().splitlines():
            if line.strip():
                entries.append(json.loads(line))
        return entries
    return _load

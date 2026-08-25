"""Shared test fixtures and constants.

FILE: tests/conftest.py

TEST_SECRET: 32+ byte HMAC key to avoid JWT InsecureKeyLengthWarning
(RFC 7518 Section 3.2 recommends ≥32 bytes for SHA256).
"""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

TEST_SECRET = "test-secret-key-that-is-at-least-32-bytes!!"


def _suite_repo_root() -> Path | None:
    """Return the git root of the repository the test suite runs in, or None.

    Tests must operate on isolated fixture repositories. This locates the repo
    that contains the suite so the HEAD guard can watch it.
    """
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=Path(__file__).parent,
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    root = out.stdout.strip()
    return Path(root) if root else None


def _head_state(repo_root: Path) -> tuple:
    """Return (branch, commit) of *repo_root*'s HEAD."""
    branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=repo_root, capture_output=True, text=True, check=True,
    ).stdout.strip()
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root, capture_output=True, text=True, check=True,
    ).stdout.strip()
    return branch, commit


def _branch_set(repo_root: Path) -> set:
    """Return the set of branch names in *repo_root*."""
    out = subprocess.run(
        ["git", "for-each-ref", "--format=%(refname:short)", "refs/heads"],
        cwd=repo_root, capture_output=True, text=True, check=True,
    ).stdout
    return {line for line in out.splitlines() if line}


@pytest.fixture(scope="session", autouse=True)
def _guard_suite_repo_unchanged():
    """Fail the suite if any test mutates the repository it runs in.

    Tests must operate on isolated fixture repositories. A test that lets the
    engine's executor create a task branch in the suite's own working tree
    (e.g. by building a graph without a fixture ``project_root``) creates a
    branch and moves HEAD, silently redirecting every subsequent commit. Record
    the suite repo's HEAD and branch set at session start and assert both are
    unchanged at session end.
    """
    repo_root = _suite_repo_root()
    if repo_root is None:
        yield
        return
    before_head = _head_state(repo_root)
    before_branches = _branch_set(repo_root)
    yield
    after_head = _head_state(repo_root)
    after_branches = _branch_set(repo_root)
    assert after_head == before_head, (
        "A test mutated the repository the test suite runs in: HEAD moved from "
        f"{before_head} to {after_head}. Tests must operate on isolated fixture "
        "repositories and never create branches in or change the checkout of "
        "the suite repository."
    )
    assert after_branches == before_branches, (
        "A test created or deleted a branch in the repository the test suite "
        f"runs in: branches changed from {sorted(before_branches)} to "
        f"{sorted(after_branches)}. Tests must operate on isolated fixture "
        "repositories."
    )


@pytest.fixture(scope="session", autouse=True)
def _isolate_tempdir(tmp_path_factory):
    """Redirect all temp allocation under a private per-session directory.

    On macOS ``$TMPDIR`` (``/var/folders/.../T``) is shared across every
    process and persists indefinitely. Tests that run ``init`` or resolve a
    project root via ``tempfile.mkdtemp()`` could otherwise write a ``.snodo``
    at — or walk up into — that shared root, tripping the nested-init guard
    for every other test and run on the machine. Pinning ``tempfile.tempdir``
    (and ``$TMPDIR`` for subprocesses) to an isolated session dir makes that
    impossible. Linux CI is unaffected (fresh ``/tmp`` per job).
    """
    root = str(tmp_path_factory.mktemp("snodo_session"))
    old_tempdir = tempfile.tempdir
    old_env = os.environ.get("TMPDIR")
    tempfile.tempdir = root
    os.environ["TMPDIR"] = root
    yield
    tempfile.tempdir = old_tempdir
    if old_env is None:
        os.environ.pop("TMPDIR", None)
    else:
        os.environ["TMPDIR"] = old_env


@pytest.fixture(autouse=True)
def isolate_snodo_home(monkeypatch):
    """Ensure no test reads/writes the real ~/.snodo/.

    Sets SNODO_HOME to a unique temp directory per test session
    so that resolve_home() never falls back to the real home dir.
    Also sets GIT_TERMINAL_PROMPT=0 so no test hangs on a git
    credential prompt (offline safety net), and SNODO_TOKEN_SECRET to a
    fixed shared secret so token issuance/verification is deterministic
    and the consumed-token store lives under the isolated SNODO_HOME.
    The fixture cleans up after itself.
    """
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    home = tempfile.mkdtemp(prefix="snodo_test_")
    monkeypatch.setenv("SNODO_HOME", home)
    monkeypatch.setenv("SNODO_TOKEN_SECRET", TEST_SECRET)
    yield
    shutil.rmtree(home, ignore_errors=True)


@pytest.fixture
def test_secret() -> str:
    """Return a 32+ byte secret for JWT signing in tests."""
    return TEST_SECRET

"""Shared test fixtures and constants.

FILE: tests/conftest.py

TEST_SECRET: 32+ byte HMAC key to avoid JWT InsecureKeyLengthWarning
(RFC 7518 Section 3.2 recommends ≥32 bytes for SHA256).
"""

import os
import tempfile
import shutil

import pytest

TEST_SECRET = "test-secret-key-that-is-at-least-32-bytes!!"


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
    The fixture cleans up after itself.
    """
    home = tempfile.mkdtemp(prefix="snodo_test_")
    monkeypatch.setenv("SNODO_HOME", home)
    yield
    shutil.rmtree(home, ignore_errors=True)


@pytest.fixture
def test_secret() -> str:
    """Return a 32+ byte secret for JWT signing in tests."""
    return TEST_SECRET

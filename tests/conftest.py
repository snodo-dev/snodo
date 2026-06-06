"""Shared test fixtures and constants.

FILE: tests/conftest.py

TEST_SECRET: 32+ byte HMAC key to avoid JWT InsecureKeyLengthWarning
(RFC 7518 Section 3.2 recommends ≥32 bytes for SHA256).
"""

import tempfile
import shutil

import pytest

TEST_SECRET = "test-secret-key-that-is-at-least-32-bytes!!"


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

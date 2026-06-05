"""Shared test fixtures and constants.

FILE: tests/conftest.py

TEST_SECRET: 32+ byte HMAC key to avoid JWT InsecureKeyLengthWarning
(RFC 7518 Section 3.2 recommends ≥32 bytes for SHA256).
"""

import pytest

TEST_SECRET = "test-secret-key-that-is-at-least-32-bytes!!"


@pytest.fixture
def test_secret() -> str:
    """Return a 32+ byte secret for JWT signing in tests."""
    return TEST_SECRET

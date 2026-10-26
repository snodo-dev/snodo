"""Direct tests for process identity start-time comparison."""

import os

import psutil
import pytest

from snodo.infrastructure.process_identity import (
    PROCESS_IDENTITY_START_TIME_TOLERANCE_SECONDS,
    process_identity_matches,
)


@pytest.mark.parametrize("difference", [1.0, -1.0, PROCESS_IDENTITY_START_TIME_TOLERANCE_SECONDS - 0.01])
def test_start_times_within_tolerance_match(monkeypatch, difference):
    monkeypatch.setattr(psutil.Process, "create_time", lambda _self: 100.0 + difference)

    assert process_identity_matches(os.getpid(), 100.0, None) is True


@pytest.mark.parametrize("difference", [PROCESS_IDENTITY_START_TIME_TOLERANCE_SECONDS + 0.01, 3600.0])
def test_start_times_beyond_tolerance_do_not_match(monkeypatch, difference):
    monkeypatch.setattr(psutil.Process, "create_time", lambda _self: 100.0 + difference)

    assert process_identity_matches(os.getpid(), 100.0, None) is False


def test_missing_start_time_is_unknown():
    assert process_identity_matches(os.getpid(), None, None) is None


def test_foreign_host_is_mismatch(monkeypatch):
    monkeypatch.setattr("snodo.infrastructure.process_identity.socket.gethostname", lambda: "local")

    assert process_identity_matches(os.getpid(), 100.0, "remote") is False

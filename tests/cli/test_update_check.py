"""Tests for the best-effort PyPI update check."""

import json
import time
from urllib.error import HTTPError
from unittest.mock import Mock

import pytest

from snodo.cli import update_check


class _Response:
    def __init__(self, version: str):
        self.payload = json.dumps({"info": {"version": version}}).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self):
        return self.payload


@pytest.fixture
def setup_check(tmp_path, monkeypatch):
    monkeypatch.setattr(update_check, "__version__", "1.2.0")
    monkeypatch.setattr(update_check, "resolve_home", lambda: tmp_path / "user-home")
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("SNODO_NO_UPDATE_CHECK", raising=False)
    return tmp_path / "user-home" / update_check._CACHE_FILE


def test_returns_newer_version(setup_check, monkeypatch):
    monkeypatch.setattr(update_check, "urlopen", lambda *_args, **_kwargs: _Response("1.3.0"))
    assert update_check.get_newer_version(is_tty=True) == "1.3.0"


@pytest.mark.parametrize("published", ["1.2.0", "1.1.9", "1.3.0rc1"])
def test_equal_older_or_prerelease_is_none(setup_check, monkeypatch, published):
    monkeypatch.setattr(update_check, "urlopen", lambda *_args, **_kwargs: _Response(published))
    assert update_check.get_newer_version(is_tty=True) is None


@pytest.mark.parametrize(
    "error",
    [TimeoutError("timeout"), HTTPError("url", 503, "unavailable", {}, None)],
)
def test_network_error_returns_none(setup_check, monkeypatch, error):
    def fail(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(update_check, "urlopen", fail)
    assert update_check.get_newer_version(is_tty=True) is None


def test_fresh_cache_avoids_network(setup_check, monkeypatch):
    setup_check.parent.mkdir(parents=True)
    setup_check.write_text(json.dumps({"checked_at": time.time(), "version": "1.4.0"}))
    request = Mock(side_effect=AssertionError("network should not be used"))
    monkeypatch.setattr(update_check, "urlopen", request)
    assert update_check.get_newer_version(is_tty=True) == "1.4.0"
    request.assert_not_called()


def test_day_old_cache_refreshes(setup_check, monkeypatch):
    setup_check.parent.mkdir(parents=True)
    setup_check.write_text(json.dumps({"checked_at": time.time() - 86400, "version": "1.4.0"}))
    request = Mock(return_value=_Response("1.5.0"))
    monkeypatch.setattr(update_check, "urlopen", request)
    assert update_check.get_newer_version(is_tty=True) == "1.5.0"
    request.assert_called_once()


@pytest.mark.parametrize("condition", ["opt_out", "ci", "not_tty"])
def test_conditions_skip_network(setup_check, monkeypatch, condition):
    request = Mock(side_effect=AssertionError("network should not be used"))
    monkeypatch.setattr(update_check, "urlopen", request)
    if condition == "opt_out":
        monkeypatch.setenv("SNODO_NO_UPDATE_CHECK", "1")
    elif condition == "ci":
        monkeypatch.setenv("CI", "1")
    assert update_check.get_newer_version(is_tty=condition != "not_tty") is None
    request.assert_not_called()

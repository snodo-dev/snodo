from __future__ import annotations

import threading
import urllib.error
import urllib.request

import pytest

from snodo.infrastructure.cloud_oauth_loopback import (
    CloudOAuthLoopbackError,
    receive_authorization_code,
)


def _run_with_callback(monkeypatch, path: str, query: str, *, state: str = "expected"):
    """Capture ephemeral port safely by intercepting server construction."""
    from snodo.infrastructure import cloud_oauth_loopback as module

    original_server = module._CallbackServer
    created = []

    class CapturingServer(original_server):
        def __init__(self, expected_state: str) -> None:
            super().__init__(expected_state)
            created.append(self)

    monkeypatch.setattr(module, "_CallbackServer", CapturingServer)

    def open_url(_authorization_url: str) -> bool:
        port = created[0].server_port

        def redirect() -> None:
            url = f"http://localhost:{port}{path}?{query}"
            try:
                urllib.request.urlopen(url, timeout=2).read()
            except urllib.error.URLError:
                pass

        threading.Thread(target=redirect, daemon=True).start()
        return True

    result = None
    error = None
    try:
        result = receive_authorization_code("https://login.example/authorize", state, timeout=1, open_browser=open_url)
    except CloudOAuthLoopbackError as exc:
        error = exc
    finally:
        assert created[0].fileno() == -1
    return result, error


def test_success_returns_code_and_localhost_redirect_uri(monkeypatch):
    result, error = _run_with_callback(monkeypatch, "/callback", "code=secret-code&state=expected")
    assert error is None
    assert result is not None
    code, redirect_uri = result
    assert code == "secret-code"
    assert redirect_uri.startswith("http://localhost:")
    assert redirect_uri.endswith("/callback")


@pytest.mark.parametrize(
    ("path", "query", "message"),
    [
        ("/callback", "code=secret-code&state=wrong", "state did not match"),
        ("/callback", "error=access_denied&state=expected", "authorization server returned an error"),
        ("/wrong", "code=secret-code&state=expected", "unexpected path"),
    ],
)
def test_invalid_callback_fails_without_returning_code(monkeypatch, path, query, message):
    result, error = _run_with_callback(monkeypatch, path, query)
    assert result is None
    assert error is not None
    assert message in str(error)


def test_timeout(monkeypatch):
    from snodo.infrastructure import cloud_oauth_loopback as module

    created = []
    original_server = module._CallbackServer

    class Capture(original_server):
        def __init__(self, state):
            super().__init__(state)
            created.append(self)

    monkeypatch.setattr(module, "_CallbackServer", Capture)
    with pytest.raises(CloudOAuthLoopbackError, match="Timed out"):
        receive_authorization_code("https://login.example", "expected", timeout=0.01, open_browser=lambda _: True)
    assert created[0].fileno() == -1


def test_prints_authorization_url_when_browser_unavailable():
    printed = []
    with pytest.raises(CloudOAuthLoopbackError, match="Timed out"):
        receive_authorization_code(
            "https://login.example/authorize?client=public",
            "expected",
            timeout=0.01,
            open_browser=lambda _: False,
            print_url=printed.append,
        )
    assert printed == ["https://login.example/authorize?client=public"]

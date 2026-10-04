from __future__ import annotations

import html
import threading
import urllib.error
import urllib.request

import pytest

from snodo.infrastructure.cloud_oauth_loopback import (
    CloudOAuthLoopbackError,
    receive_authorization_code,
    receive_pasted_authorization_code,
)


def _run_with_callback(monkeypatch, path: str, query: str, *, state: str = "expected"):
    """Capture ephemeral port safely by intercepting server construction."""
    from snodo.infrastructure import cloud_oauth_loopback as module

    original_server = module._CallbackServer
    created = []
    pages = []
    requests = []

    class CapturingServer(original_server):
        def __init__(self, expected_state: str) -> None:
            super().__init__(expected_state)
            created.append(self)

    monkeypatch.setattr(module, "_CallbackServer", CapturingServer)

    def open_url(_authorization_url: str) -> bool:
        port = created[0].server_port

        def redirect() -> None:
            url = f"http://127.0.0.1:{port}{path}?{query}"
            try:
                with urllib.request.urlopen(url, timeout=2) as response:
                    pages.append((response.headers.get_content_type(), response.read().decode()))
            except urllib.error.URLError:
                pages.append(("request failed", ""))

        request = threading.Thread(target=redirect, daemon=True)
        requests.append(request)
        request.start()
        return True

    result = None
    error = None
    try:
        result = receive_authorization_code("https://login.example/authorize", state, timeout=1, open_browser=open_url)
    except CloudOAuthLoopbackError as exc:
        error = exc
    finally:
        assert created[0].fileno() == -1
        for request in requests:
            request.join(timeout=2)
    return result, error, pages[0]


def test_success_returns_code_and_localhost_redirect_uri(monkeypatch):
    result, error, (content_type, page) = _run_with_callback(monkeypatch, "/callback", "code=secret-code&state=expected")
    assert error is None
    assert result is not None
    code, redirect_uri = result
    assert code == "secret-code"
    assert redirect_uri.startswith("http://localhost:")
    assert redirect_uri.endswith("/callback")
    assert content_type == "text/html"
    assert "You're signed in to snodo cloud" in html.unescape(page)
    assert "go back to the terminal and close this tab" in page


@pytest.mark.parametrize(
    ("path", "query", "message"),
    [
        ("/callback", "code=secret-code&state=wrong", "state did not match"),
        ("/callback", "error=access_denied&state=expected", "authorization server returned an error"),
        ("/wrong", "code=secret-code&state=expected", "unexpected path"),
    ],
)
def test_invalid_callback_fails_without_returning_code(monkeypatch, path, query, message):
    result, error, (content_type, page) = _run_with_callback(monkeypatch, path, query)
    assert result is None
    assert error is not None
    assert message in str(error)
    assert content_type == "text/html"
    assert "Sign-in couldn't be completed" in html.unescape(page)
    assert "snodo cloud login" in page


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


@pytest.mark.parametrize("pasted", ["auth-code#expected", "auth-code"])
def test_pasted_code_accepts_complete_or_bare_code(pasted):
    printed = []
    code, redirect = receive_pasted_authorization_code(
        "https://login.example/authorize", "expected", print_url=printed.append,
        prompt=lambda _message: pasted,
    )
    assert code == "auth-code"
    assert redirect == "https://mcp-auth.snodo.dev/cli/code"
    assert printed == ["https://login.example/authorize"]


def test_pasted_code_rejects_wrong_state():
    with pytest.raises(CloudOAuthLoopbackError, match="state did not match"):
        receive_pasted_authorization_code(
            "https://login.example/authorize", "expected", print_url=lambda _: None,
            prompt=lambda _: "auth-code#wrong",
        )

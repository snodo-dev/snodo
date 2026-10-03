"""One-shot localhost callback listener for cloud OAuth authorization."""

from __future__ import annotations

import http.server
import socket
import threading
import time
import urllib.parse
import webbrowser
from collections.abc import Callable


class CloudOAuthLoopbackError(RuntimeError):
    """Safe, credential-free failure during the local OAuth redirect."""


class _CallbackServer(http.server.HTTPServer):
    address_family = socket.AF_INET
    allow_reuse_address = False

    def __init__(self, state: str) -> None:
        self.state = state
        self.result: str | CloudOAuthLoopbackError | None = None
        self.received = threading.Event()
        super().__init__(("127.0.0.1", 0), _CallbackHandler)


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    server: _CallbackServer

    def do_GET(self) -> None:  # noqa: N802
        if self.client_address[0] not in {"127.0.0.1", "::1"}:
            self._reply("This callback is available only on this device.")
            return

        parsed = urllib.parse.urlsplit(self.path)
        query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        if parsed.path != "/callback":
            self._finish_error("OAuth callback used an unexpected path.", "Unexpected callback path.")
        elif query.get("state", [None])[0] != self.server.state:
            self._finish_error("OAuth callback state did not match.", "Sign-in could not be verified.")
        elif query.get("error", [None])[0]:
            self._finish_error("The authorization server returned an error.", "Sign-in was not completed.")
        elif not query.get("code", [None])[0]:
            self._finish_error("OAuth callback did not include an authorization code.", "Sign-in did not return a code.")
        else:
            self.server.result = query["code"][0]
            self.server.received.set()
            self._reply("Sign-in complete. You can close this tab.")

    def _finish_error(self, message: str, page: str) -> None:
        self.server.result = CloudOAuthLoopbackError(message)
        self.server.received.set()
        self._reply(page)

    def _reply(self, message: str) -> None:
        body = f"<!doctype html><html><body><p>{message}</p></body></html>".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        # The request target can contain the authorization code.
        return


def receive_authorization_code(
    authorization_url: str | Callable[[str], str],
    state: str,
    *,
    timeout: float = 180.0,
    open_browser: Callable[[str], bool] | None = None,
    print_url: Callable[[str], object] = print,
) -> tuple[str, str]:
    """Open authorization URL, await its localhost callback, and return code and redirect URI.

    A browser opener and URL printer may be injected to make the flow testable.
    The returned redirect URI uses ``localhost`` even though the listener binds
    only to the IPv4 loopback interface.
    """
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    server = _CallbackServer(state)
    redirect_uri = f"http://localhost:{server.server_port}/callback"
    url = authorization_url(redirect_uri) if callable(authorization_url) else authorization_url
    opener = open_browser or webbrowser.open
    try:
        try:
            opened = opener(url)
        except Exception:
            opened = False
        if not opened:
            print_url(url)
        server.timeout = 0.2
        # handle_request permits clean deadline checks without a worker thread.
        expires = time.monotonic() + timeout
        while not server.received.is_set() and time.monotonic() < expires:
            server.handle_request()
        if not server.received.is_set():
            raise CloudOAuthLoopbackError("Timed out waiting for the OAuth callback.")
        if isinstance(server.result, CloudOAuthLoopbackError):
            raise server.result
        assert isinstance(server.result, str)
        return server.result, redirect_uri
    finally:
        server.server_close()

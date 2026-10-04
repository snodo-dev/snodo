"""One-shot localhost callback listener for cloud OAuth authorization."""

from __future__ import annotations

import html
import http.server
import socket
import threading
import time
import urllib.parse
import webbrowser
from collections.abc import Callable


class CloudOAuthLoopbackError(RuntimeError):
    """Safe, credential-free failure during the local OAuth redirect."""


def receive_pasted_authorization_code(
    authorization_url: str,
    state: str,
    *,
    print_url: Callable[[str], object] = print,
    prompt: Callable[[str], str] = input,
) -> tuple[str, str]:
    """Print the cloud authorization URL and validate the code pasted by the user."""
    print_url(authorization_url)
    pasted = prompt("Paste the code shown in your browser: ").strip()
    code, separator, returned_state = pasted.partition("#")
    if not code:
        raise CloudOAuthLoopbackError("No authorization code was pasted.")
    if separator and returned_state != state:
        raise CloudOAuthLoopbackError("Pasted authorization code state did not match.")
    return code, "https://mcp-auth.snodo.dev/cli/code"


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
            self._reply(False, "This callback is available only on this device.")
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
            self._reply(True)

    def _finish_error(self, message: str, page: str) -> None:
        self.server.result = CloudOAuthLoopbackError(message)
        self.server.received.set()
        self._reply(False, page)

    def _reply(self, success: bool, message: str = "") -> None:
        if success:
            heading = "You're signed in to snodo cloud"
            detail = "You can go back to the terminal and close this tab."
            label = "SNODO CLOUD · SIGN-IN COMPLETE"
        else:
            heading = "Sign-in couldn't be completed"
            detail = f"{html.escape(message)} Please run <code>snodo cloud login</code> again."
            label = "SNODO CLOUD · SIGN-IN"
        body = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(heading)}</title>
  <style>
    :root {{ color-scheme: light dark; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    body {{ box-sizing: border-box; min-height: 100vh; margin: 0; padding: 24px; display: grid; place-items: center; background: #f4f7f7; color: #172729; }}
    main {{ width: min(100%, 480px); box-sizing: border-box; padding: clamp(24px, 7vw, 48px); border: 1px solid #dce7e6; border-radius: 18px; background: #fff; box-shadow: 0 18px 60px #193c3b12; }}
    .label {{ margin: 0 0 22px; color: #087e78; font: 600 11px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace; letter-spacing: .12em; }}
    h1 {{ margin: 0; font-size: clamp(25px, 7vw, 34px); line-height: 1.15; letter-spacing: -.035em; }}
    .detail {{ margin: 16px 0 0; color: #536365; font-size: 16px; line-height: 1.65; }}
    code {{ color: inherit; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: .9em; }}
    @media (prefers-color-scheme: dark) {{
      body {{ background: #101718; color: #edf5f4; }}
      main {{ background: #182223; border-color: #2c3a3b; box-shadow: 0 18px 60px #0005; }}
      .label {{ color: #5ad3c8; }}
      .detail {{ color: #b3c2c1; }}
    }}
  </style>
</head>
<body><main>
  <p class="label">{html.escape(label)}</p>
  <h1>{html.escape(heading)}</h1>
  <p class="detail">{detail}</p>
</main></body>
</html>""".encode("utf-8")
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

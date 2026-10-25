from __future__ import annotations

import asyncio
import base64
import logging
from pathlib import Path

import pytest

from snodo.infrastructure.cloud_relay import CloudRelay, machine_id, websocket_url
from snodo.infrastructure.relay_frame_codec import decode_frame


class Socket:
    def __init__(self):
        self.sent = []

    async def send(self, value):
        self.sent.append(decode_frame(value))


class Response:
    status_code = 200
    headers = {"content-type": "text/event-stream"}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def aiter_bytes(self):
        yield b"event: one\ndata: first\n\n"
        yield b"event: two\ndata: second\n\n" + (b"x" * (192 * 1024 + 1))


class Http:
    def stream(self, method, url, *, headers, content):
        assert method == "POST"
        assert url == "http://127.0.0.1:8765/path"
        assert content == b"request"
        return Response()


def test_dispatch_streams_response_chunks_without_logging_credentials():
    captured = []
    logger = logging.getLogger("relay-test")
    logger.addHandler(logging.StreamHandler(type("Capture", (), {"emit": lambda self, record: captured.append(record.getMessage())})()))
    relay = CloudRelay({}, registry=lambda: [{"hostname": "api.test", "port": 8765, "transport": "ws"}],
                       http_client=Http(), machine="mid", logger=logger)
    socket = Socket()
    asyncio.run(relay._dispatch(socket, {"id": "r1", "hostname": "api.test", "method": "POST",
                                   "path": "/path", "headers": {"authorization": "secret"}}, b"request"))
    assert [frame["t"] for frame in socket.sent] == ["res", "chunk", "chunk", "chunk", "chunk"]
    assert socket.sent[0]["status"] == 200
    payload = b"".join(base64.b64decode(frame["data_b64"]) for frame in socket.sent[1:])
    assert payload == b"event: one\ndata: first\n\nevent: two\ndata: second\n\n" + (b"x" * (192 * 1024 + 1))
    assert len(socket.sent[1:]) == 4
    assert socket.sent[-1]["last"] is True
    assert not any(secret in line for line in captured for secret in ("secret", "request"))


def test_machine_id_persists_with_private_permissions(tmp_path: Path):
    path = tmp_path / "state" / "machine_id"
    first = machine_id(path)
    second = machine_id(path)
    assert first == second
    assert path.stat().st_mode & 0o777 == 0o600


def test_websocket_url_uses_configured_cloud_address():
    assert websocket_url({"cloud": {"lease_url": "https://cloud.example/base"}}, "machine") == (
        "wss://cloud.example/runner/connect?machine_id=machine"
    )


def test_endpoints_advertise_only_ws_entries():
    from snodo.infrastructure.cloud_relay import _endpoints

    entries = [
        {"hostname": "ws.test", "port": 1, "transport": "ws"},
        {"hostname": "cf.test", "port": 2, "transport": "cloudflare"},
        {"hostname": "old.test", "port": 3},
    ]
    assert _endpoints(entries) == [{"hostname": "ws.test", "auth": ["oauth"]}]


def test_dispatch_does_not_forward_cloudflare_only_hostname():
    relay = CloudRelay({}, registry=lambda: [{"hostname": "cf.test", "port": 8765, "transport": "cloudflare"}],
                       http_client=Http(), machine="mid")
    socket = Socket()
    asyncio.run(relay._dispatch(socket, {"id": "r1", "hostname": "cf.test", "method": "POST",
                                          "path": "/path", "headers": {}}, b"request"))
    assert socket.sent == [{"t": "err", "id": "r1", "code": "unknown_endpoint", "message": "Unknown endpoint"}]


class CapturingHttp(Http):
    def stream(self, method, url, *, headers, content):
        self.headers = headers
        return Response()


@pytest.mark.parametrize("hostname", ["private.test", "public-pub.snodo.dev"])
def test_dispatch_routes_private_and_public_hosts_to_same_entry(hostname):
    http = CapturingHttp()
    relay = CloudRelay({}, registry=lambda: [{
        "hostname": "private.test", "public_hostname": "public-pub.snodo.dev",
        "port": 8765, "transport": "ws",
    }], http_client=http, machine="mid")
    socket = Socket()
    asyncio.run(relay._dispatch(socket, {
        "id": "r1", "hostname": hostname, "method": "POST", "path": "/path",
        "headers": {"hOsT": "attacker.test", ":authority": "also-attacker.test", "authorization": "token"},
    }, b"request"))
    assert socket.sent[0]["t"] == "res"
    assert http.headers == {"authorization": "token", "Host": hostname}


def test_dispatch_refuses_unrelated_hostname():
    relay = CloudRelay({}, registry=lambda: [{
        "hostname": "private.test", "public_hostname": "public-pub.snodo.dev",
        "port": 8765, "transport": "ws",
    }], http_client=Http(), machine="mid")
    socket = Socket()
    asyncio.run(relay._dispatch(socket, {
        "id": "r1", "hostname": "unrelated.test", "method": "POST",
        "path": "/path", "headers": {},
    }, b"request"))
    assert socket.sent == [{"t": "err", "id": "r1", "code": "unknown_endpoint", "message": "Unknown endpoint"}]

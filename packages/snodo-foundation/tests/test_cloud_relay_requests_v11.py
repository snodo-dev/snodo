from __future__ import annotations

import asyncio

from snodo.infrastructure.cloud_relay import CloudRelay
from snodo.infrastructure.relay_frame_codec import decode_frame


class Socket:
    def __init__(self, incoming):
        self.incoming = [__import__('json').dumps(frame) for frame in incoming]
        self.sent = []
    async def send(self, value):
        self.sent.append(decode_frame(value))
    def __aiter__(self):
        return self
    async def __anext__(self):
        if not self.incoming:
            raise StopAsyncIteration
        return self.incoming.pop(0)


class Response:
    status_code = 200
    headers = {}
    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass
    async def aiter_bytes(self):
        yield b"reply"


class Http:
    def __init__(self): self.calls = []
    def stream(self, method, url, *, headers, content):
        self.calls.append((method, url, content))
        return Response()


def test_get_empty_body_dispatches_once_after_terminal_chunk():
    async def exercise():
        http = Http()
        relay = CloudRelay({}, registry=lambda: [{"hostname": "api", "port": 80}], http_client=http, machine="m")
        sock = Socket([
            {"t":"req", "id":"g", "hostname":"api", "method":"GET", "path":"/", "headers":{}},
            {"t":"chunk", "id":"g", "seq":0, "last":True, "data_b64":""},
        ])
        # Drive only the request-frame handling by using a fake connection setup.
        async def lease(): return type("Lease", (), {"token":"irrelevant"})()
        relay.lease_fetch = lease
        class ContextSocket(Socket):
            close_code = None
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
        cloud = ContextSocket(sock.incoming)
        async def connect(*a, **k): return cloud
        relay.socket_connect = connect
        relay._fetch_lease = lease
        # avoid registry/hello loop extras
        await relay._connection()
        await asyncio.gather(*list(relay._tasks.values()), return_exceptions=True)
        assert http.calls == [("GET", "http://127.0.0.1:80/", b"")]
        assert not relay._tasks and not relay._request_frames and not relay._cancelled
    asyncio.run(exercise())

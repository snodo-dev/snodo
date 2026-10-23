from __future__ import annotations

import asyncio
import base64
import json

import httpx
import pytest

from snodo.infrastructure.cloud_relay import CloudRelay
from snodo.infrastructure.relay_frame_codec import decode_frame


class Socket:
    def __init__(self, incoming=()):
        self.incoming = [json.dumps(item) for item in incoming]
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
    headers = {"content-type": "text/event-stream"}

    def __init__(self, pieces=(b"reply",)):
        self.pieces = pieces

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def aiter_bytes(self):
        for piece in self.pieces:
            yield piece


class Http:
    def __init__(self, response=None, error=None):
        self.calls = []
        self.response = response or Response()
        self.error = error

    def stream(self, method, url, *, headers, content):
        self.calls.append((method, url, headers, content))
        if self.error:
            raise self.error
        return self.response


def chunk(rid, seq, data=b"", last=False):
    return {
        "t": "chunk",
        "id": rid,
        "seq": seq,
        "last": last,
        "data_b64": base64.b64encode(data).decode(),
    }


def request(rid="r", **kw):
    return {
        "t": "req",
        "id": rid,
        "hostname": "api",
        "method": "GET",
        "path": "/",
        "headers": {},
        **kw,
    }


def make_relay(http, entries=None, **kw):
    return CloudRelay(
        {},
        registry=lambda: (
            [{"hostname": "api", "port": 8080}] if entries is None else entries
        ),
        http_client=http,
        machine="m",
        **kw,
    )


def test_get_empty_last_chunk_dispatches_once_and_cleans_state():
    async def run():
        http = Http()
        relay = make_relay(http)
        sock = Socket()
        await relay._dispatch(sock, request(), b"")
        assert len(http.calls) == 1 and http.calls[0][3] == b""
        assert not relay._tasks and not relay._request_frames and not relay._cancelled

    asyncio.run(run())


def test_post_body_is_reassembled_before_single_dispatch():
    async def run():
        http = Http()
        relay = make_relay(http)
        incoming = [
            request("p", method="POST"),
            chunk("p", 0, b"one"),
            chunk("p", 1, b"two", True),
        ]
        sock = Socket(incoming)
        # Exercise the same reassembler contract used by connection dispatch.
        from snodo.infrastructure.relay_frame_codec import ChunkReassembler

        assembler = ChunkReassembler("p")
        assert assembler.add(incoming[1]) is None
        body = assembler.add(incoming[2])
        assert body == b"onetwo"
        await relay._dispatch(sock, incoming[0], body)
        assert len(http.calls) == 1 and http.calls[0][3] == b"onetwo"

    asyncio.run(run())


def test_large_and_streamed_replies_are_ordered_and_bounded():
    async def run():
        data = b"x" * (192 * 1024 + 9)
        relay = make_relay(Http(Response((data[:100], data[100:]))))
        sock = Socket()
        await relay._dispatch(sock, request(), b"")
        chunks = [f for f in sock.sent if f["t"] == "chunk"]
        assert [len(base64.b64decode(f["data_b64"])) for f in chunks] == [
            100,
            192 * 1024 - 91,
            0,
        ]
        assert [f["seq"] for f in chunks] == [0, 1, 2] and chunks[-1]["last"]
        assert b"".join(base64.b64decode(f["data_b64"]) for f in chunks) == data
        assert [f["t"] for f in sock.sent[:1]] == ["res"]

    asyncio.run(run())


@pytest.mark.parametrize(
    "error",
    [
        httpx.ConnectError("refused"),
        httpx.ReadTimeout("timeout"),
        httpx.ReadError("reset"),
    ],
)
def test_local_failures_emit_safe_error_and_release(error):
    async def run():
        relay = make_relay(Http(error=error))
        sock = Socket()
        await relay._dispatch(
            sock, request(headers={"Authorization": "secret"}), b"private body"
        )
        errs = [f for f in sock.sent if f["t"] == "err"]
        assert (
            len(errs) == 1
            and "secret" not in json.dumps(sock.sent)
            and "private body" not in json.dumps(sock.sent)
        )
        assert not relay._tasks and not relay._request_frames and not relay._cancelled

    asyncio.run(run())


def test_unknown_endpoint_chunk_and_out_of_order_rejected():
    async def run():
        relay = make_relay(Http(), entries=[])
        sock = Socket()
        await relay._dispatch(sock, request(), b"")
        assert sock.sent[0]["code"] == "unknown_endpoint"
        from snodo.infrastructure.relay_frame_codec import ChunkReassembler

        assembler = ChunkReassembler("x")
        with pytest.raises(ValueError):
            assembler.add(chunk("x", 1, b"oops", True))
        assert not relay._tasks and not relay._request_frames and not relay._cancelled

    asyncio.run(run())


def test_ninth_request_waits_for_slot_and_cancelled_request_releases_state():
    async def run():
        entered = 0
        release = asyncio.Event()
        started = asyncio.Event()

        class BlockingHttp:
            def stream(self, *args, **kwargs):
                class CM:
                    async def __aenter__(self):
                        nonlocal entered
                        entered += 1
                        if entered == 8:
                            started.set()
                        await release.wait()
                        return Response(())

                    async def __aexit__(self, *args):
                        pass

                return CM()

        relay = make_relay(BlockingHttp())
        sock = Socket()
        tasks = [
            asyncio.create_task(relay._dispatch(sock, request(str(i)), b""))
            for i in range(9)
        ]
        await started.wait()
        await asyncio.sleep(0)
        assert entered == 8
        release.set()
        await asyncio.gather(*tasks)
        assert entered == 9
        assert not relay._tasks and not relay._request_frames and not relay._cancelled

    asyncio.run(run())


def test_unknown_hostname_emits_error_and_no_state_remains():
    async def run():
        relay = make_relay(Http(), entries=[])
        sock = Socket()
        await relay._dispatch(sock, request(), b"")
        assert sock.sent[0]["t"] == "err"
        assert not relay._tasks and not relay._request_frames and not relay._cancelled

    asyncio.run(run())

from __future__ import annotations

import asyncio
import json

import pytest
import websockets

from snodo.infrastructure.cloud_relay import CloudRelay


class Http:
    def __init__(self):
        self.called = asyncio.Event()

    def stream(self, *args, **kwargs):
        owner = self

        class Response:
            status_code = 200
            headers = {}

            async def __aenter__(self):
                owner.called.set()
                return self

            async def __aexit__(self, *args):
                pass

            async def aiter_bytes(self):
                yield b"ok"

        return Response()


async def serve_once(handler):
    server = await websockets.serve(handler, "127.0.0.1", 0)
    return server, f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"


def test_loopback_idle_connection_dispatches_later_request():
    async def scenario():
        connected = asyncio.Event()
        open_after_idle = []
        http = Http()

        async def handler(ws):
            await ws.recv()  # hello
            connected.set()
            await asyncio.sleep(0.7)  # several registry polls
            open_after_idle.append(ws.state.name == "OPEN")
            for frame in (
                {"t": "req", "id": "r", "hostname": "api", "method": "POST", "path": "/", "headers": {}},
                {"t": "chunk", "id": "r", "seq": 0, "last": True, "data_b64": "eA=="},
            ):
                await ws.send(json.dumps(frame))
            await asyncio.wait_for(http.called.wait(), 0.5)

        server, url = await serve_once(handler)
        relay = CloudRelay(
            {"cloud": {"lease_url": url}}, lease_fetch=_lease, machine="m",
            http_client=http, registry=lambda: [{"hostname": "api", "port": 1}],
        )
        task = asyncio.create_task(relay._connection())
        await asyncio.wait_for(connected.wait(), 0.5)
        await asyncio.wait_for(http.called.wait(), 1.5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        server.close()
        await server.wait_closed()
        assert open_after_idle == [True]
        assert not relay._tasks and not relay._request_frames and not relay._cancelled

    asyncio.run(scenario())


def _lease(*args, **kwargs):
    from types import SimpleNamespace
    return SimpleNamespace(token="test-token")


def test_connection_teardown_cancels_inflight_dispatch_and_cleans_tables():
    async def scenario():
        started = asyncio.Event()

        class BlockingHttp:
            def stream(self, *args, **kwargs):
                class CM:
                    async def __aenter__(self):
                        started.set()
                        await asyncio.Event().wait()
                    async def __aexit__(self, *args):
                        pass
                return CM()

        async def handler(ws):
            await ws.recv()
            for frame in (
                {"t": "req", "id": "r", "hostname": "api", "method": "GET", "path": "/", "headers": {}},
                {"t": "chunk", "id": "r", "seq": 0, "last": True, "data_b64": ""},
            ):
                await ws.send(json.dumps(frame))
            await started.wait()

        server, url = await serve_once(handler)
        relay = CloudRelay({"cloud": {"lease_url": url}}, lease_fetch=_lease, machine="m",
                           http_client=BlockingHttp(), registry=lambda: [{"hostname": "api", "port": 1}])
        await relay._connection()
        server.close()
        await server.wait_closed()
        assert not relay._tasks and not relay._request_frames and not relay._cancelled

    asyncio.run(scenario())


def test_loopback_normal_close_reconnects_with_backoff_and_4001_stops():
    async def scenario():
        connections = 0
        second = asyncio.Event()

        async def handler(ws):
            nonlocal connections
            connections += 1
            await ws.recv()
            if connections == 1:
                await ws.close()
            else:
                second.set()
                await ws.close()

        server, url = await serve_once(handler)
        sleeps = []

        async def sleep(delay):
            sleeps.append(delay)
            await asyncio.sleep(0.8)

        relay = CloudRelay({"cloud": {"lease_url": url}}, lease_fetch=_lease,
                           machine="m", sleep=sleep, http_client=Http())
        await asyncio.wait_for(relay.run(max_connections=2), 2.5)
        assert connections == 2
        assert len(sleeps) == 2 and 0.8 <= sleeps[0] <= 1.2
        assert 1.6 <= sleeps[1] <= 2.4
        server.close()
        await server.wait_closed()

        replaced_count = 0

        async def replaced(ws):
            nonlocal replaced_count
            replaced_count += 1
            await ws.recv()
            await ws.close(code=4001)

        server, url = await serve_once(replaced)
        relay = CloudRelay({"cloud": {"lease_url": url}}, lease_fetch=_lease,
                           machine="m", sleep=sleep, http_client=Http())
        before = len(sleeps)
        await asyncio.wait_for(relay.run(), 1.5)
        assert replaced_count == 1
        assert len(sleeps) == before
        server.close()
        await server.wait_closed()

    asyncio.run(scenario())

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

from snodo.infrastructure.cloud_relay import CloudRelay
from snodo.infrastructure.relay_frame_codec import decode_frame


class FakeSocket:
    def __init__(self):
        self.sent = []
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.closed = True

    async def send(self, value):
        self.sent.append(decode_frame(value))

    def __aiter__(self):
        async def incoming():
            while not self.closed:
                await asyncio.sleep(10)
                if False:
                    yield b""
        return incoming()


class StatusError(Exception):
    def __init__(self, status):
        self.status_code = status


def _lease(token="not-for-logs"):
    return SimpleNamespace(token=token)


def test_hello_reannounced_after_idle_registry_change():
    sockets = []
    entries = [[]]

    async def connect(*args, **kwargs):
        socket = FakeSocket()
        sockets.append(socket)
        return socket

    async def lease(*args, **kwargs):
        return _lease()

    relay = CloudRelay({}, socket_connect=connect, lease_fetch=lease,
                       registry=lambda: entries[-1], machine="machine")
    async def scenario():
        task = asyncio.create_task(relay._connection())
        await asyncio.sleep(.01)
        entries.append([{"hostname": "api.test", "port": 1234}])
        await asyncio.sleep(.3)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    asyncio.run(scenario())
    assert [frame["t"] for frame in sockets[0].sent] == ["hello", "hello"]
    assert sockets[0].sent[0]["endpoints"] == []
    assert sockets[0].sent[1]["endpoints"] == [{"hostname": "api.test", "auth": ["oauth"]}]


def test_fresh_lease_per_reconnect_and_401_retries_once():
    leases = []
    attempts = []

    async def fetch(*args, **kwargs):
        leases.append(kwargs.get("force", False))
        return _lease(f"secret-{len(leases)}")

    async def connect(url, **kwargs):
        attempts.append(kwargs["additional_headers"]["Authorization"])
        if len(attempts) == 1:
            raise StatusError(401)
        return FakeSocket()

    relay = CloudRelay({}, socket_connect=connect, lease_fetch=fetch, machine="m")
    asyncio.run(relay.run(max_connections=2))
    assert leases == [False, True, False]
    assert attempts == ["Bearer secret-1", "Bearer secret-2", "Bearer secret-3"]


def test_missing_lease_retries_and_logs_plain_message_only():
    captured = []
    logger = logging.getLogger("relay-connection-test")
    handler = logging.Handler()
    handler.emit = lambda record: captured.append(record.getMessage())
    logger.handlers = [handler]
    logger.setLevel(logging.WARNING)
    calls, sleeps = [], []
    async def fetch(*args, **kwargs):
        calls.append(1)
        return None
    async def sleep(value):
        sleeps.append(value)
    relay = CloudRelay({}, lease_fetch=fetch, sleep=sleep, machine="m", logger=logger)
    asyncio.run(relay.run(max_connections=1))
    assert calls == [1] and sleeps
    assert "sign in with the cloud login" in " ".join(captured)
    assert not any(secret in line for line in captured for secret in ("Bearer", "secret", "credential"))


def test_403_and_426_stop_without_reconnect():
    for status in (403, 426):
        calls = []
        async def fetch(*args, **kwargs): return _lease()
        async def connect(*args, **kwargs):
            calls.append(1)
            raise StatusError(status)
        relay = CloudRelay({}, socket_connect=connect, lease_fetch=fetch, machine="m")
        asyncio.run(relay.run())
        assert calls == [1]

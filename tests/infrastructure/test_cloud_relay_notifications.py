import asyncio

import pytest

from snodo.infrastructure.cloud_relay import CloudRelay


class _Socket:
    close_code = None

    def __init__(self):
        self.sent = []
        self.iterations = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def send(self, value):
        self.sent.append(value)

    def __aiter__(self):
        return self

    async def __anext__(self):
        self.iterations += 1
        if self.iterations == 1:
            await asyncio.sleep(0.3)
        raise StopAsyncIteration


def test_connected_notification_is_once_before_endpoint_change_hello():
    async def run():
        messages = []
        socket = _Socket()
        registries = iter([
            [],
            [{"hostname": "one.example", "port": 80, "transport": "ws"}],
        ])
        relay = CloudRelay(
            {},
            machine="machine",
            lease_fetch=lambda *_args, **_kwargs: object(),
            socket_connect=lambda *_args, **_kwargs: _connected_socket(socket),
            registry=lambda: next(registries, [{"hostname": "one.example", "port": 80, "transport": "ws"}]),
            notify=messages.append,
        )
        relay._fetch_lease = _lease
        await relay._connection()
        assert messages == ["Connected to snodo cloud.", "Connection to snodo cloud was lost. Retrying."]
        assert len(socket.sent) == 2
    asyncio.run(run())

async def _connected_socket(socket):
    return socket


async def _lease():
    return type("Lease", (), {"token": "token"})()


def test_notify_exception_is_ignored():
    relay = CloudRelay.__new__(CloudRelay)
    relay.notify = lambda _message: (_ for _ in ()).throw(RuntimeError("notify failure"))
    relay.log = __import__("logging").getLogger(__name__)
    relay._notify("connected")


@pytest.mark.parametrize(
    ("lease", "expected"),
    [(None, "Cloud login is missing or expired. Sign in to snodo cloud.")],
)
def test_missing_lease_notification(lease, expected):
    async def run():
        messages = []
        relay = CloudRelay({}, machine="machine", lease_fetch=lambda *_args, **_kwargs: lease, notify=messages.append)
        with pytest.raises(ConnectionError):
            await relay._connection()
        assert messages == [expected]
    asyncio.run(run())

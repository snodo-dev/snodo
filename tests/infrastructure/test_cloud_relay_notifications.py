import asyncio

import pytest

from snodo.infrastructure.cloud_relay import CloudRelay, _endpoints


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


@pytest.mark.parametrize("status", [403, 426])
def test_rejected_connection_notification(status):
    async def run():
        messages = []

        class Rejected(Exception):
            status_code = status

        async def connect(*_args, **_kwargs):
            raise Rejected()

        relay = CloudRelay(
            {}, machine="machine", lease_fetch=lambda *_args, **_kwargs: object(),
            socket_connect=connect, notify=messages.append,
            sleep=lambda _delay: asyncio.sleep(0),
        )
        relay._fetch_lease = _lease
        await relay.run(max_connections=1)
        assert messages == ["snodo cloud rejected the connection."]

    asyncio.run(run())


def test_replaced_connection_notification():
    async def run():
        messages = []

        class Replaced(Exception):
            code = 4001

        async def connect(*_args, **_kwargs):
            raise Replaced()

        relay = CloudRelay({}, machine="machine", socket_connect=connect, notify=messages.append)
        relay._fetch_lease = _lease
        await relay.run(max_connections=1)
        assert messages == ["This connection was replaced by a newer runner connection."]

    asyncio.run(run())


def test_connection_lost_notification():
    async def run():
        messages = []
        socket = _Socket()
        relay = CloudRelay(
            {}, machine="machine", lease_fetch=lambda *_args, **_kwargs: object(),
            socket_connect=lambda *_args, **_kwargs: _connected_socket(socket),
            registry=lambda: [], notify=messages.append,
        )
        relay._fetch_lease = _lease
        await relay._connection()
        assert messages[-1] == "Connection to snodo cloud was lost. Retrying."

    asyncio.run(run())


def test_raising_notify_does_not_break_connection_loop():
    async def run():
        socket = _Socket()
        calls = []

        def notify(message):
            calls.append(message)
            raise RuntimeError("notify failure")

        relay = CloudRelay(
            {}, machine="machine", lease_fetch=lambda *_args, **_kwargs: object(),
            socket_connect=lambda *_args, **_kwargs: _connected_socket(socket),
            registry=lambda: [], notify=notify,
        )
        relay._fetch_lease = _lease
        await relay._connection()
        assert calls == ["Connected to snodo cloud.", "Connection to snodo cloud was lost. Retrying."]

    asyncio.run(run())


def test_hello_endpoints_include_public_alias_only_for_ws_entries():
    assert _endpoints([
        {"hostname": "private.example", "port": 80, "transport": "ws", "public_hostname": "alias-pub.snodo.dev"},
        {"hostname": "plain.example", "port": 80, "transport": "ws"},
        {"hostname": "cloudflare.example", "port": 80, "transport": "cloudflare", "public_hostname": "ignored.example"},
    ]) == [
        {"hostname": "private.example", "auth": ["oauth"]},
        {"hostname": "alias-pub.snodo.dev", "auth": ["oauth"]},
        {"hostname": "plain.example", "auth": ["oauth"]},
    ]


@pytest.mark.parametrize("next_alias", ["first-pub.snodo.dev", "changed-pub.snodo.dev"])
def test_hello_is_resent_when_public_alias_changes(next_alias):
    async def run():
        socket = _Socket()
        entry = {"hostname": "private.example", "port": 80, "transport": "ws"}
        calls = 0

        def registry():
            nonlocal calls
            calls += 1
            return [entry] if calls == 1 else [dict(entry, public_hostname=next_alias)]

        relay = CloudRelay(
            {}, machine="machine", lease_fetch=lambda *_args, **_kwargs: object(),
            socket_connect=lambda *_args, **_kwargs: _connected_socket(socket), registry=registry,
        )
        relay._fetch_lease = _lease
        await relay._connection()
        import json
        frames = [json.loads(message) for message in socket.sent]
        assert [frame["endpoints"] for frame in frames if frame["t"] == "hello"] == [
            [{"hostname": "private.example", "auth": ["oauth"]}],
            [
                {"hostname": "private.example", "auth": ["oauth"]},
                {"hostname": next_alias, "auth": ["oauth"]},
            ],
        ]
    asyncio.run(run())

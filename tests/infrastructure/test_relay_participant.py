import asyncio
import threading
import time

from snodo.infrastructure.relay_participant import RelayParticipant


class FakeRelay:
    def __init__(self, *, terminal=False):
        self.started = threading.Event()
        self.closed = threading.Event()
        self.terminal = terminal

    async def run(self):
        self.started.set()
        if not self.terminal:
            await asyncio.Event().wait()

    async def close(self):
        self.closed.set()


def wait_for(event, timeout=2):
    assert event.wait(timeout), "timed out waiting for relay state"


def test_waiter_takes_over_when_owner_stops(tmp_path):
    owner_relay, waiter_relay = FakeRelay(), FakeRelay()
    lock = tmp_path / "machine_id.relay.lock"
    owner = RelayParticipant(lambda: owner_relay, lock_path=lock, retry_interval=0.01)
    waiter = RelayParticipant(lambda: waiter_relay, lock_path=lock, retry_interval=0.01)
    owner.start()
    try:
        wait_for(owner_relay.started)
        waiter.start()
        assert not waiter_relay.started.is_set()
        owner.stop()
        wait_for(waiter_relay.started)
    finally:
        owner.stop()
        waiter.stop()
    assert owner_relay.closed.is_set()
    assert waiter_relay.closed.is_set()


def test_waiting_and_acquisition_are_reported_once(tmp_path):
    owner_relay, waiter_relay = FakeRelay(), FakeRelay()
    lock = tmp_path / "machine_id.relay.lock"
    messages = []
    owner = RelayParticipant(lambda: owner_relay, lock_path=lock, retry_interval=0.01)
    waiter = RelayParticipant(lambda: waiter_relay, lock_path=lock, retry_interval=0.01,
                              notify=messages.append)
    owner.start()
    try:
        wait_for(owner_relay.started)
        waiter.start()
        deadline = time.monotonic() + 2
        while not messages and time.monotonic() < deadline:
            time.sleep(0.005)
        assert len(messages) == 1
        assert "Another snodo serve" in messages[0]
        time.sleep(0.05)
        assert len(messages) == 1
        owner.stop()
        wait_for(waiter_relay.started)
        assert len(messages) == 2
        assert "now holds" in messages[1]
    finally:
        owner.stop()
        waiter.stop()


def test_lock_free_participant_does_not_notify(tmp_path):
    relay = FakeRelay()
    messages = []
    participant = RelayParticipant(lambda: relay, lock_path=tmp_path / "lock",
                                  notify=messages.append)
    participant.start()
    try:
        wait_for(relay.started)
        assert messages == []
    finally:
        participant.stop()


def test_raising_notify_does_not_stop_participant(tmp_path):
    relay = FakeRelay()
    lock = tmp_path / "lock"
    owner_relay = FakeRelay()
    owner = RelayParticipant(lambda: owner_relay, lock_path=lock, retry_interval=0.01)
    notified = threading.Event()

    def broken_notify(_message):
        notified.set()
        raise RuntimeError("notification failed")

    participant = RelayParticipant(lambda: relay, lock_path=lock, retry_interval=0.01,
                                   notify=broken_notify)
    owner.start()
    wait_for(owner_relay.started)
    participant.start()
    try:
        wait_for(notified)
        owner.stop()
        wait_for(relay.started)
    finally:
        owner.stop()
        participant.stop()


def test_stop_before_start_and_double_stop_are_safe(tmp_path):
    participant = RelayParticipant(FakeRelay, lock_path=tmp_path / "lock")
    participant.stop()
    participant.start()
    participant.stop()
    participant.stop()


def test_stopped_waiter_never_starts_relay(tmp_path):
    first, second = FakeRelay(), FakeRelay()
    lock = tmp_path / "lock"
    owner = RelayParticipant(lambda: first, lock_path=lock, retry_interval=0.01)
    waiter = RelayParticipant(lambda: second, lock_path=lock, retry_interval=0.01)
    owner.start()
    try:
        wait_for(first.started)
        waiter.start()
        waiter.stop()
        owner.stop()
        time.sleep(0.05)
        assert not second.started.is_set()
    finally:
        owner.stop()
        waiter.stop()


def test_terminal_relay_is_not_restarted(tmp_path):
    relays = []

    def factory():
        relay = FakeRelay(terminal=True)
        relays.append(relay)
        return relay

    participant = RelayParticipant(factory, lock_path=tmp_path / "lock", retry_interval=0.01)
    participant.start()
    try:
        deadline = time.monotonic() + 2
        while not relays and time.monotonic() < deadline:
            time.sleep(0.005)
        assert len(relays) == 1
        time.sleep(0.05)
        assert len(relays) == 1
    finally:
        participant.stop()

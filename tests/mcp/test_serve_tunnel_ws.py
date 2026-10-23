"""Focused lifecycle tests for WS relay supervision."""

import signal
from types import SimpleNamespace

from snodo.cli.commands.serve_tunnel_ws import run_ws_tunnel


def test_interrupt_stops_relay_and_child():
    calls = []
    handlers = {}
    child = SimpleNamespace(poll=lambda: None)

    class Participant:
        def start(self):
            calls.append("start")

        def stop(self):
            calls.append("stop")

    def signal_set(sig, handler):
        handlers[sig] = handler
        return None

    def sleep(_):
        handlers[signal.SIGINT](signal.SIGINT, None)

    assert run_ws_tunnel(
        child, "host.example", participant=Participant(),
        terminate=lambda proc: calls.append("terminate"), print_active=lambda _: None,
        sleep=sleep, signal_module=SimpleNamespace(signal=signal_set, SIGINT=signal.SIGINT,
                                                    SIGTERM=signal.SIGTERM),
    ) == 0
    assert calls == ["start", "stop", "terminate"]


def test_child_exit_stops_relay_and_rolls_back():
    calls = []
    child = SimpleNamespace(poll=lambda: 1)

    class Participant:
        def start(self):
            calls.append("start")

        def stop(self):
            calls.append("stop")

    signals = SimpleNamespace(signal=lambda *_: None, SIGINT=signal.SIGINT, SIGTERM=signal.SIGTERM)
    assert run_ws_tunnel(
        child, "host.example", participant=Participant(),
        terminate=lambda proc: calls.append("terminate"), print_active=lambda _: None,
        rollback=lambda: calls.append("rollback"), signal_module=signals,
    ) == 1
    assert calls == ["start", "stop", "terminate", "rollback"]

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


def test_created_participant_notifications_reach_stdout(monkeypatch, capsys):
    from snodo.infrastructure import cloud_relay, relay_participant

    captured = {}

    class Participant:
        def __init__(self, relay_factory, *, notify):
            captured["notify"] = notify
            captured["relay_factory"] = relay_factory

        def start(self):
            captured["notify"]("connected to snodo cloud")

        def stop(self):
            pass

    monkeypatch.setattr(relay_participant, "RelayParticipant", Participant)
    monkeypatch.setattr(cloud_relay, "CloudRelay", lambda config, *, notify: captured.update(relay_notify=notify))
    monkeypatch.setattr("snodo.config.ConfigManager.load", lambda self: {})

    run_ws_tunnel(
        SimpleNamespace(poll=lambda: 1), "host.example",
        terminate=lambda _: None, rollback=lambda: None, print_active=lambda _: None,
    )

    assert capsys.readouterr().out.startswith("connected to snodo cloud\n")
    captured["relay_factory"]()
    assert captured["relay_notify"] is captured["notify"]

"""Lifecycle supervision for a managed WebSocket tunnel."""

import signal
import time


def run_ws_tunnel(mcp_process, hostname, *, participant=None, terminate, rollback=None,
                  print_active, sleep=time.sleep, signal_module=signal) -> int:
    """Supervise relay participation alongside an already-bound MCP child."""
    if participant is None:
        from snodo.infrastructure.relay_participant import RelayParticipant
        from snodo.infrastructure.cloud_relay import CloudRelay
        from snodo.config import ConfigManager
        def notify(message):
            print(message, flush=True)
        participant = RelayParticipant(
            lambda: CloudRelay(ConfigManager().load(), notify=notify), notify=notify
        )
    try:
        participant.start()
    except Exception:
        terminate(mcp_process)
        if rollback:
            rollback()
        raise
    print_active(hostname)
    stopping = False

    def cleanup(*_):
        nonlocal stopping
        stopping = True
        print("\nStopping...")
        try:
            participant.stop()
        finally:
            terminate(mcp_process)

    old_int = signal_module.signal(signal_module.SIGINT, cleanup)
    old_term = signal_module.signal(signal_module.SIGTERM, cleanup)
    try:
        while not stopping:
            if mcp_process.poll() is not None:
                print("Error: MCP server exited unexpectedly.", file=__import__("sys").stderr)
                cleanup()
                if rollback:
                    rollback()
                return 1
            sleep(0.1)
    except KeyboardInterrupt:
        cleanup()
        return 0
    finally:
        signal_module.signal(signal_module.SIGINT, old_int)
        signal_module.signal(signal_module.SIGTERM, old_term)
    return 0

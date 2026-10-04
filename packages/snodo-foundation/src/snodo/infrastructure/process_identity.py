"""Best-effort verification that a PID still belongs to its recorded run."""

import socket
from typing import Optional


def process_identity_matches(pid: object, started_at: object, host: object) -> Optional[bool]:
    """Return identity match, mismatch, or None when identity is unavailable.

    ``None`` deliberately leaves callers to apply their recent-activity rule.
    """
    if started_at is None:
        return None
    if host is not None and host != socket.gethostname():
        return False
    try:
        import psutil
        actual = psutil.Process(int(pid)).create_time()
        expected = float(started_at)
    except Exception:  # noqa: BLE001 — identity is unanswerable on probe/parse failure
        return None
    return abs(actual - expected) < 0.01

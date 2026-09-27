"""Safe forwarding helpers for managed-tunnel child output."""

import re
from typing import Any


def forward_child_line(line: str, stream: Any, secrets: tuple[str, ...] = ()) -> None:
    """Forward a verbose child line after credential redaction."""
    try:
        from snodo.mcp.transport import sanitize_verbose_output

        safe_line = sanitize_verbose_output(line, secrets)
        if safe_line:
            print(safe_line, file=stream, flush=True)
    except Exception:  # noqa: BLE001, S110 — output forwarding must not stop the tunnel
        pass


def tunnel_secret_values(config: dict, api_key: str) -> tuple[str, ...]:
    """Collect known tunnel credentials for exact-match child-output redaction."""
    values = [api_key]

    def visit(value: Any, key: str = "") -> None:
        if isinstance(value, dict):
            for child_key, child in value.items():
                visit(child, str(child_key))
        elif isinstance(value, (list, tuple)):
            for child in value:
                visit(child, key)
        elif isinstance(value, str) and value and re.search(
            r"(?i)(token|secret|api[_-]?key|credential|client[_-]?id)", key,
        ):
            values.append(value)

    visit(config)
    return tuple(dict.fromkeys(value for value in values if value))

"""Build and validate protocols for the demo fixture."""

from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path
import subprocess
from typing import Any

import yaml


def governed_protocol_text() -> str:
    """Return the bugfix-surgeon template shipped with the installed Snodo."""
    return (
        files("snodo.protocols.templates")
        .joinpath("bugfix-surgeon.yml")
        .read_text(encoding="utf-8")
    )


def bare_protocol_text() -> str:
    """Return the shipped protocol with review and recovery disabled."""
    protocol: dict[str, Any] = yaml.safe_load(governed_protocol_text())
    mode = next(
        mode
        for mode in protocol["modes"]
        if mode["mode_id"] == protocol["initial_mode"]
    )
    mode["validators"] = [
        validator_id for validator_id in mode["validators"] if validator_id == "triage"
    ]
    protocol["execution"]["max_recovery_depth"] = 0
    return yaml.safe_dump(protocol, sort_keys=False, allow_unicode=True)


def write_protocol(protocol_text: str, path: str | Path) -> Path:
    """Write protocol text to *path* and return the resulting path."""
    destination = Path(path)
    destination.write_text(protocol_text, encoding="utf-8")
    return destination


def validate_protocol(path: str | Path) -> tuple[bool | None, Any]:
    """Validate a protocol with Snodo, returning validity and CLI findings.

    ``None`` validity indicates that the Snodo executable is unavailable.
    """
    try:
        result = subprocess.run(  # noqa: S603
            ["snodo", "protocol", "validate", str(path), "--json"],  # noqa: S607
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return None, {"unavailable": "snodo command is not available"}

    try:
        findings = json.loads(result.stdout)
    except json.JSONDecodeError:
        findings = result.stderr.strip() or result.stdout.strip()
    return result.returncode == 0, findings

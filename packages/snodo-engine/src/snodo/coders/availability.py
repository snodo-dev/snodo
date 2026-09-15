"""Shared coder-availability check: can this coder be invoked HERE?

FILE: snodo/coders/availability.py

One ``shutil.which`` per declared requirement — costs nothing, and it must
run in the process that will invoke the coder. Readiness performs the same
check for the operator's shell, but a background job inherits the environment
of the process that dispatched it (the MCP server, whose PATH may differ from
the shell that ran ``snodo ready``). The check made where it is not needed is
worthless without this one made where it is: refuse a dispatch here, before
any validation or execution cost is spent.

Adapters DECLARE their requirements on the Coder ABC
(``availability_requirements``); this module only reads them. It adds no new
dependency edge: dispatch layers (snodo-mcp) already sit above snodo.coders
in the import contracts, and readiness is left untouched.
"""

import logging
import shutil
import subprocess
from typing import Optional, Tuple

_logger = logging.getLogger(__name__)


def read_binary_version(
    binary_path: str, version_args: tuple = ("--version",),
) -> str:
    """Return the version *binary_path* reports, or "" if it will not say.

    Best-effort by design: a tool without a version flag, or one that hangs,
    must not turn a run (or a readiness check) into a failure over an audit
    detail. The probe is bounded, needs no shell, and reduces output to its
    last non-empty line — where version strings conventionally land. One
    implementation, shared by the adapters that record provenance and by the
    readiness checker that reports it, so the two can never disagree about
    what "the version" is (Fixes #290).
    """
    if not binary_path or not version_args:
        return ""
    try:
        proc = subprocess.run(  # noqa: S603 - argv list (no shell); resolved absolute path plus fixed flags
            [binary_path, *version_args],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=5,
        )
    except Exception:  # noqa: BLE001 — a best-effort audit probe must never break the run it records
        _logger.debug("Could not read version from %s", binary_path, exc_info=True)
        return ""
    output = (proc.stdout or "").strip() or (proc.stderr or "").strip()
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    return lines[-1] if lines else ""


def check_coder_available(coder_name: str) -> Optional[Tuple[str, str]]:
    """Return ``(missing_binary, remediation)`` if *coder_name* cannot run here.

    *remediation* is the adapter's operator-facing install command (never
    empty). Returns None when the coder is invocable in this process's
    environment, and also when *coder_name* is not a registered coder — an
    unknown name is the resolver's problem to report, not a PATH problem to
    guess at.
    """
    from snodo.coders import CODER_REGISTRY

    adapter_cls = CODER_REGISTRY.get(coder_name)
    if adapter_cls is None:
        return None
    for binary, remediation in adapter_cls.availability_requirements():
        if not shutil.which(binary):
            return (binary, remediation or f"Install {binary} and make sure it is on PATH")
    return None

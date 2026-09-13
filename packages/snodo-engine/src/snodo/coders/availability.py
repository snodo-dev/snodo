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

import shutil
from typing import Optional, Tuple


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

"""Shared JSON output helpers for snodo's machine interface (ADR 022).

Every ``--json`` command emits a single JSON object to stdout carrying a
``schema`` field of the form ``snodo.<command>.v<N>``.  A consumer checks the
schema field to detect a breaking change before parsing the rest.  Field names
are stable and asserted by the test suite — a rename fails the suite rather
than a downstream consumer.

Exit codes distinguish the four validation outcomes so a caller can branch
without parsing prose:

    pass            0
    blocker         1
    escalate        2
    validator_error 3
    internal_error  4

Human output is unchanged: ``--json`` is additive and only affects stdout.
"""

import json
import sys

# The schema version shared by every machine-interface command.  Bump it when a
# field is renamed, removed, or changes meaning — never silently.
SCHEMA_VERSION = 1

# Exit codes for the four validation outcomes (plus internal error).
EXIT_PASS = 0
EXIT_BLOCKER = 1
EXIT_ESCALATE = 2
EXIT_VALIDATOR_ERROR = 3
EXIT_INTERNAL_ERROR = 4

OUTCOME_EXIT_CODES = {
    "pass": EXIT_PASS,
    "blocker": EXIT_BLOCKER,
    "escalate": EXIT_ESCALATE,
    "validator_error": EXIT_VALIDATOR_ERROR,
    "internal_error": EXIT_INTERNAL_ERROR,
}


def schema_name(command: str) -> str:
    """Return the versioned schema identifier for *command* (e.g. ``status``)."""
    return f"snodo.{command}.v{SCHEMA_VERSION}"


def emit_json(payload: dict, exit_code: int = EXIT_PASS) -> int:
    """Print *payload* as JSON to stdout and return *exit_code*.

    The payload must already carry its ``schema`` field.  Errors are written to
    stderr so stdout stays a single, parseable JSON document.
    """
    print(json.dumps(payload, indent=2, sort_keys=True))
    return exit_code


def emit_error(command: str, message: str, exit_code: int = EXIT_INTERNAL_ERROR) -> int:
    """Emit a JSON error object to stdout and return *exit_code*.

    Used when a ``--json`` command cannot produce its normal payload (e.g. not
    inside a project, missing argument).  The shape is uniform so a consumer
    can always parse stdout.
    """
    return emit_json(
        {
            "schema": schema_name(command),
            "ok": False,
            "error": message,
        },
        exit_code=exit_code,
    )


def print_error(message: str) -> None:
    """Write a human-facing error to stderr (never stdout)."""
    print(message, file=sys.stderr)

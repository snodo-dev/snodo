"""Acceptance: a fresh project from every template runs its first task.

FILE: tests/e2e/test_ungated_first_task.py

Every template ships a default test command that runs on a POSIX shell and
exits zero, so a project initialised in a directory with no marker file for any
supported stack is never blocked on an unresolvable test command. Auto-detection
and `--test-command` take precedence over that default; the default is only what
remains when neither produces anything.

The hard constraint is honesty in the tamper-evident audit log: when the no-op
default runs, the quality validator records outcome "no_tests" and states that no
tests were executed — it never claims a pass for work that did not run.
"""

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e


def _verification_records(snodo_cli):
    """Return the data payloads of every verification_executed audit event."""
    audit_path = Path(snodo_cli.home) / ".snodo" / "audit.log"
    if not audit_path.exists():
        return []
    records = []
    for line in audit_path.read_text().splitlines():
        if line.strip():
            ev = json.loads(line)
            if ev.get("event_type") == "verification_executed":
                records.append(ev["data"])
    return records


@pytest.mark.parametrize(
    "template,setup_args",
    [
        ("solo", []),
        ("team", []),
        ("greenfield", [["mode", "change", "build"]]),
    ],
)
def test_ungated_first_task_runs_to_completion(snodo_cli, template, setup_args):
    """A markerless project's first task completes and the audit record does
    not claim tests passed."""
    r = snodo_cli(["init", "--template", template, "--yes"])
    assert r.returncode == 0, r.stderr

    for args in setup_args:
        r = snodo_cli(args)
        assert r.returncode == 0, r.stderr

    r = snodo_cli(["run", "implement a hello world function", "--mock"])
    assert r.returncode == 0, (
        f"fresh {template} project must not be blocked on its first task:\n"
        f"stdout={r.stdout}\nstderr={r.stderr}"
    )

    # The ungated run is visible in the run output, not only in the log.
    combined = r.stdout + r.stderr
    assert "pass (skipped)" in combined or "ran no tests" in combined, (
        f"ungated {template} run must be visible in the run output, got:\n{combined}"
    )

    # The audit trail must state plainly that no tests were executed.
    records = _verification_records(snodo_cli)
    assert records, f"{template} run produced no verification_executed events"
    for rec in records:
        assert rec["outcome"] == "no_tests", (
            f"{template} ungated run must not claim a pass, got outcome "
            f"{rec['outcome']!r}"
        )


@pytest.mark.e2e
def test_2plus_n_quality_is_not_the_blocker(snodo_cli):
    """2+n's strict global constraints (files_in_scope, tests_exist) block the
    mock coder's fixture files — that is orthogonal to the test command. Even
    so, the quality validator must resolve its default, run it, and record that
    no tests were executed rather than erroring on an unresolvable command."""
    r = snodo_cli(["init", "--template", "2+n", "--yes"])
    assert r.returncode == 0, r.stderr

    r = snodo_cli(["run", "implement a user registration endpoint", "--mock"])
    # Blocked by the files_in_scope / tests_exist constraints, not by quality.
    assert r.returncode == 1, r.stderr
    assert "files_in_scope" in r.stdout or "tests_exist" in r.stdout

    records = _verification_records(snodo_cli)
    assert records, "2+n run produced no verification_executed events"
    for rec in records:
        assert rec["outcome"] == "no_tests", (
            f"2+n ungated quality run must not claim a pass, got "
            f"{rec['outcome']!r}"
        )

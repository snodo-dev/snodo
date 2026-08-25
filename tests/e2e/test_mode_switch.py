"""Regression: `snodo run` must honour the active mode, not initial_mode.

FILE: tests/e2e/test_mode_switch.py

Before the fix, `snodo run` passed `protocol.initial_mode` to the closure
driver while `_resolve_session` used `state.current_mode`, so a multi-mode
protocol always executed its initial mode's validators regardless of
`snodo mode change`. This fails open: the wrong gate runs and the task is
reported as completed.
"""

import json

import pytest


def _halt_payload(stdout: str) -> dict:
    """Extract the structured halt payload JSON from a run's stdout."""
    start = stdout.index("--- STRUCTURED HALT PAYLOAD ---") + len("--- STRUCTURED HALT PAYLOAD ---")
    end = stdout.index("--- END STRUCTURED HALT PAYLOAD ---")
    return json.loads(stdout[start:end])


@pytest.mark.e2e
def test_run_honours_switched_mode(snodo_cli):
    """After `mode change scaffold`, the run executes scaffold's validators."""
    r = snodo_cli(["init", "--template", "greenfield", "--yes"])
    assert r.returncode == 0, r.stderr

    r = snodo_cli(["mode", "change", "scaffold"])
    assert r.returncode == 0, r.stderr

    r = snodo_cli(["run", "establish the toolchain", "--mock"])
    # warn stubs under unanimous → ESCALATE → exit 1 (not a success)
    assert r.returncode == 1, r.stderr

    payload = _halt_payload(r.stdout)
    assert payload["current_mode"] == "scaffold"

    validator_ids = {v["validator_id"] for v in payload.get("validator_results", [])}
    pre_validator_ids = {v["validator_id"] for v in payload.get("pre_validation", {}).get("validator_results", [])}
    assert pre_validator_ids == {"meta-spec", "architecture"}
    assert "decision-record" not in validator_ids
    assert "security" not in validator_ids


@pytest.mark.e2e
def test_session_mode_equals_execution_mode(snodo_cli):
    """The created session's mode matches the mode the run executed."""
    r = snodo_cli(["init", "--template", "greenfield", "--yes"])
    assert r.returncode == 0, r.stderr

    r = snodo_cli(["mode", "change", "scaffold"])
    assert r.returncode == 0, r.stderr

    r = snodo_cli(["run", "establish the toolchain", "--mock"])
    assert r.returncode == 1, r.stderr

    # The session id embeds the mode prefix (scaf for scaffold).
    assert "sess_" in r.stdout
    assert "_scaf_" in r.stdout


@pytest.mark.e2e
def test_single_mode_protocol_unaffected(snodo_cli):
    """A single-mode protocol still runs its only mode."""
    r = snodo_cli(["init", "--template", "solo", "--yes"])
    assert r.returncode == 0, r.stderr

    r = snodo_cli(["run", "implement a hello world function", "--mock"])
    assert r.returncode == 1, r.stderr

    payload = _halt_payload(r.stdout)
    assert payload["current_mode"] == "producer"

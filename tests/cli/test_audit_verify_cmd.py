"""Tests for `snodo audit verify` — the operator surface for the hash chain.

verify_chain() has always been snodo's integrity gate but nothing called it.
These cover the surface: a pass is *printed* (a gate nobody sees pass is not a
gate), a failure names where the chain broke, the exit-code contract is
0/1/4, and --json emits the versioned machine object.
"""

import json
from types import SimpleNamespace
from unittest.mock import patch

from snodo.cli.commands.audit_cmd import audit_verify_command
from snodo.cli.json_output import EXIT_BLOCKER, EXIT_INTERNAL_ERROR, EXIT_PASS
from snodo.infrastructure.audit import AuditLog


def _parse_stdout(capsys):
    return json.loads(capsys.readouterr().out)


def _build_chain(tmp_path, n=3):
    log = AuditLog(str(tmp_path / ".snodo" / "audit.log"))
    for i in range(n):
        log.append_event(f"evt_{i}", {"index": i})
    return log


def _run(tmp_path, json_out=False):
    with patch("snodo.infrastructure.paths.resolve_project_root", return_value=str(tmp_path)):
        return audit_verify_command(SimpleNamespace(json=json_out))


# ---------------------------------------------------------------------------
# Human surface
# ---------------------------------------------------------------------------

def test_valid_chain_prints_ok_and_count(tmp_path, capsys):
    _build_chain(tmp_path, 3)
    assert _run(tmp_path) == EXIT_PASS
    out = capsys.readouterr().out
    assert "Audit chain: OK" in out
    assert "3 event(s) verified" in out


def test_empty_log_is_vacuously_intact_and_exit_zero(tmp_path, capsys):
    # No log file at all.
    assert _run(tmp_path) == EXIT_PASS
    out = capsys.readouterr().out
    assert "Audit chain: OK" in out
    assert "empty" in out


def test_tampered_chain_fails_and_names_the_problem(tmp_path, capsys):
    log = _build_chain(tmp_path, 3)
    path = log.log_path
    # Tamper the on-disk payload so its stored hash no longer matches.
    lines = path.read_text().splitlines()
    rec = json.loads(lines[0])
    rec["data"] = {"index": 999}
    lines[0] = json.dumps(rec)
    path.write_text("\n".join(lines) + "\n")

    assert _run(tmp_path) == EXIT_BLOCKER
    err = capsys.readouterr().err
    assert "Audit chain: FAILED" in err
    # The operator gets a reason, not just a failure.
    assert "reason:" in err


# ---------------------------------------------------------------------------
# JSON surface
# ---------------------------------------------------------------------------

def test_json_valid_shape(tmp_path, capsys):
    _build_chain(tmp_path, 2)
    assert _run(tmp_path, json_out=True) == EXIT_PASS
    data = _parse_stdout(capsys)
    assert set(data.keys()) == {
        "schema", "ok", "valid", "log_path", "event_count",
        "reason", "sequence", "detail",
    }
    assert data["schema"] == "snodo.audit_verify.v1"
    assert data["valid"] is True
    assert data["ok"] is True
    assert data["event_count"] == 2
    assert data["reason"] is None


def test_json_invalid_shape_and_exit(tmp_path, capsys):
    log = _build_chain(tmp_path, 2)
    lines = log.log_path.read_text().splitlines()
    rec = json.loads(lines[0])
    rec["previous_hash"] = "f" * 64  # break the chain on disk
    lines[0] = json.dumps(rec)
    log.log_path.write_text("\n".join(lines) + "\n")

    assert _run(tmp_path, json_out=True) == EXIT_BLOCKER
    data = _parse_stdout(capsys)
    assert data["valid"] is False
    assert data["ok"] is True  # the check ran; the verdict is valid:false
    assert data["reason"]  # non-empty reason present
    assert data["detail"]


def test_not_in_project_is_internal_error(tmp_path, capsys):
    with patch("snodo.infrastructure.paths.resolve_project_root", return_value=None):
        assert audit_verify_command(SimpleNamespace(json=False)) == EXIT_INTERNAL_ERROR
    assert "Not inside a snodo project." in capsys.readouterr().err


def test_not_in_project_json(tmp_path, capsys):
    with patch("snodo.infrastructure.paths.resolve_project_root", return_value=None):
        assert audit_verify_command(SimpleNamespace(json=True)) == EXIT_INTERNAL_ERROR
    data = _parse_stdout(capsys)
    assert data["ok"] is False
    assert data["schema"] == "snodo.audit_verify.v1"


# ---------------------------------------------------------------------------
# The command is discoverable as `snodo audit verify`.
# ---------------------------------------------------------------------------

def test_command_is_mounted_under_audit_group():
    import snodo.cli.main as cli_main

    audit_group = next(
        (g for g in cli_main.app.registered_groups if g.name == "audit"), None
    )
    assert audit_group is not None, "audit command group is not mounted"
    cmds = [c.name for c in audit_group.typer_instance.registered_commands]
    assert "verify" in cmds

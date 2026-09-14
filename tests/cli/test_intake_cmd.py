"""CLI tests for intake: proposals are offered, writes are acceptance-gated.

FILE: tests/cli/test_intake_cmd.py

Intake is the one survey-adjacent path that may write to the protocol, so the
tests hold the gate rather than the prose: a rejected run and a JSON run leave
every byte where it was, an accepted run writes exactly the accepted criteria
with no duplication, and an unknown target validator writes nothing.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from snodo.cli.commands import intake_cmd
from tests.survey.protocol_fixtures import protocol_yaml

RULE_RECORD = """\
# ADR 001 — Derive org_id server-side

## Context

The org id used to be taken from the request body.

## Decision

The org_id is derived server-side from the API key lookup, never accepted from
the request.

## Consequences

Queries are slower.

## Alternatives considered

Trust the client: rejected.
"""

SECOND_RULE_RECORD = """\
# ADR 002 — Scope every query

## Decision

Every D1 query carries an org filter.
"""

NON_RULE_RECORD = """\
# ADR 003 — Naming

## Context

We needed a name.

## Consequences

The name is long.

## Alternatives considered

A shorter name: rejected.
"""


def _make_repo(root: Path, records: dict) -> Path:
    from git import Repo

    Repo.init(str(root))
    decisions = root / "docs" / "decisions"
    decisions.mkdir(parents=True)
    for name, text in records.items():
        (decisions / name).write_text(text)
    snodo = root / ".snodo"
    snodo.mkdir()
    protocol_path = snodo / "protocol.yml"
    protocol_path.write_text(protocol_yaml())
    return protocol_path


def _args(**overrides) -> SimpleNamespace:
    base = {
        "validator": None,
        "json": False,
        "accept_all": False,
        "reject_all": False,
        "no_input": False,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class TestProposals:
    def test_json_reports_each_rule_with_its_record_and_writes_nothing(
        self, tmp_path, monkeypatch, capsys
    ):
        protocol_path = _make_repo(
            tmp_path, {"001-rule.md": RULE_RECORD, "003-naming.md": NON_RULE_RECORD}
        )
        before = protocol_path.read_bytes()
        monkeypatch.chdir(tmp_path)

        rc = intake_cmd.intake_command(_args(json=True))

        payload = json.loads(capsys.readouterr().out)
        assert rc == 0
        assert payload["schema"] == "snodo.intake.v1"
        assert payload["written"] is False
        assert [p["record_path"] for p in payload["proposals"]] == [
            "docs/decisions/001-rule.md"
        ]
        assert "org_id is derived server-side" in payload["proposals"][0]["criterion"]
        assert protocol_path.read_bytes() == before

    def test_a_record_that_states_no_rule_is_not_proposed(
        self, tmp_path, monkeypatch, capsys
    ):
        protocol_path = _make_repo(tmp_path, {"003-naming.md": NON_RULE_RECORD})
        before = protocol_path.read_bytes()
        monkeypatch.chdir(tmp_path)

        rc = intake_cmd.intake_command(_args(json=True))

        payload = json.loads(capsys.readouterr().out)
        assert rc == 0
        assert payload["proposals"] == []
        assert protocol_path.read_bytes() == before


class TestAcceptanceGate:
    def test_rejecting_writes_nothing(self, tmp_path, monkeypatch, capsys):
        protocol_path = _make_repo(tmp_path, {"001-rule.md": RULE_RECORD})
        before = protocol_path.read_bytes()
        monkeypatch.chdir(tmp_path)

        rc = intake_cmd.intake_command(_args(reject_all=True))

        assert rc == 0
        assert "nothing written" in capsys.readouterr().out.casefold()
        assert protocol_path.read_bytes() == before

    def test_a_rejected_proposal_writes_no_criterion(self, tmp_path, monkeypatch):
        protocol_path = _make_repo(tmp_path, {"001-rule.md": RULE_RECORD})
        before = protocol_path.read_bytes()
        monkeypatch.chdir(tmp_path)

        rc = intake_cmd.intake_command(
            _args(), ask=lambda _prompt: "n"
        )

        assert rc == 0
        assert protocol_path.read_bytes() == before

    def test_accepting_writes_only_the_accepted_criteria(self, tmp_path, monkeypatch):
        protocol_path = _make_repo(
            tmp_path,
            {"001-rule.md": RULE_RECORD, "002-scope.md": SECOND_RULE_RECORD},
        )
        monkeypatch.chdir(tmp_path)

        answers = iter(["n", "y"])
        rc = intake_cmd.intake_command(_args(), ask=lambda _prompt: next(answers))

        assert rc == 0
        text = protocol_path.read_text()
        assert "Every D1 query carries an org filter." in text
        assert "org_id is derived server-side" not in text

    def test_accept_all_appends_to_the_default_validator(self, tmp_path, monkeypatch):
        protocol_path = _make_repo(tmp_path, {"001-rule.md": RULE_RECORD})
        monkeypatch.chdir(tmp_path)

        rc = intake_cmd.intake_command(_args(accept_all=True))

        assert rc == 0
        assert "org_id is derived server-side" in protocol_path.read_text()
        # The fixture's only architecture-typed validator is `adr`.
        assert "validator_id: adr" in protocol_path.read_text()

    def test_an_unknown_target_validator_writes_nothing(self, tmp_path, monkeypatch, capsys):
        protocol_path = _make_repo(tmp_path, {"001-rule.md": RULE_RECORD})
        before = protocol_path.read_bytes()
        monkeypatch.chdir(tmp_path)

        rc = intake_cmd.intake_command(
            _args(accept_all=True, validator="does-not-exist")
        )

        assert rc == 4
        assert "does-not-exist" in capsys.readouterr().err
        assert protocol_path.read_bytes() == before

    def test_non_interactive_without_a_decision_refuses_and_writes_nothing(
        self, tmp_path, monkeypatch, capsys
    ):
        protocol_path = _make_repo(tmp_path, {"001-rule.md": RULE_RECORD})
        before = protocol_path.read_bytes()
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(intake_cmd.sys.stdin, "isatty", lambda: False)

        rc = intake_cmd.intake_command(_args(no_input=True))

        assert rc == 4
        assert protocol_path.read_bytes() == before

    def test_interactive_acceptance_reads_the_operator_answer(
        self, tmp_path, monkeypatch
    ):
        import builtins

        protocol_path = _make_repo(tmp_path, {"001-rule.md": RULE_RECORD})
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(intake_cmd.sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr(builtins, "input", lambda _prompt: "y")

        rc = intake_cmd.intake_command(_args())

        assert rc == 0
        assert "org_id is derived server-side" in protocol_path.read_text()

    def test_an_explicit_validator_receives_the_criteria(self, tmp_path, monkeypatch):
        import yaml

        protocol_path = _make_repo(tmp_path, {"001-rule.md": RULE_RECORD})
        monkeypatch.chdir(tmp_path)

        rc = intake_cmd.intake_command(_args(accept_all=True, validator="quality"))

        assert rc == 0
        data = yaml.safe_load(protocol_path.read_text())
        quality = next(v for v in data["validators"] if v["validator_id"] == "quality")
        assert any(
            "org_id is derived server-side" in c for c in quality["criteria"]
        )

    def test_accept_all_and_reject_all_together_write_nothing(
        self, tmp_path, monkeypatch, capsys
    ):
        protocol_path = _make_repo(tmp_path, {"001-rule.md": RULE_RECORD})
        before = protocol_path.read_bytes()
        monkeypatch.chdir(tmp_path)

        rc = intake_cmd.intake_command(_args(accept_all=True, reject_all=True))

        assert rc == 4
        assert "mutually exclusive" in capsys.readouterr().err
        assert protocol_path.read_bytes() == before

    def test_no_records_proposes_nothing_and_writes_nothing(
        self, tmp_path, monkeypatch, capsys
    ):
        protocol_path = _make_repo(tmp_path, {})
        before = protocol_path.read_bytes()
        monkeypatch.chdir(tmp_path)

        rc = intake_cmd.intake_command(_args())

        assert rc == 0
        assert "No proposable criteria" in capsys.readouterr().out
        assert protocol_path.read_bytes() == before


class TestIntakeErrors:
    def test_missing_protocol_reports_an_error_and_writes_nothing(
        self, tmp_path, monkeypatch, capsys
    ):
        from git import Repo

        Repo.init(str(tmp_path))
        (tmp_path / "docs" / "decisions").mkdir(parents=True)
        (tmp_path / "docs" / "decisions" / "001-rule.md").write_text(RULE_RECORD)
        monkeypatch.chdir(tmp_path)

        rc = intake_cmd.intake_command(_args())

        assert rc == 4
        assert "protocol.yml" in capsys.readouterr().err

    def test_an_unloadable_protocol_reports_an_error(self, tmp_path, monkeypatch, capsys):
        from git import Repo

        Repo.init(str(tmp_path))
        (tmp_path / ".snodo").mkdir()
        (tmp_path / ".snodo" / "protocol.yml").write_text("protocol: exists\n")
        monkeypatch.chdir(tmp_path)

        rc = intake_cmd.intake_command(_args())

        assert rc == 4
        assert capsys.readouterr().err

    def test_not_a_git_repository_reports_json_error(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)

        rc = intake_cmd.intake_command(_args(json=True))

        assert rc == 4
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is False


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
    def test_accepting_preserves_protocol_comments_and_appends_in_target(self, tmp_path, monkeypatch):
        import yaml
        from snodo.compiler.models import Protocol
        from snodo.compiler.verifier import verify_protocol

        protocol_path = _make_repo(
            tmp_path, {"001-rule.md": RULE_RECORD, "002-scope.md": SECOND_RULE_RECORD}
        )
        original = protocol_path.read_text()
        original = original.replace(
            "protocol_id: monorepo",
            "# protocol header comment\nprotocol_id: monorepo",
        ).replace(
            "  - validator_id: adr\n",
            "  - validator_id: adr\n    # target validator comment\n",
        ).replace(
            "  - validator_id: quality\n",
            "  - validator_id: quality\n    # other validator comment\n",
        )
        protocol_path.write_text(original)
        monkeypatch.chdir(tmp_path)

        rc = intake_cmd.intake_command(_args(accept_all=True, validator="adr"))

        assert rc == 0
        updated = protocol_path.read_text()
        additions = {
            "  - The org_id is derived server-side from the API key lookup, never accepted from the request.\n",
            "  - Every D1 query carries an org filter.\n",
        }
        assert "".join(line for line in updated.splitlines(keepends=True) if line not in additions) == original
        data = yaml.safe_load(updated)
        target = next(v for v in data["validators"] if v["validator_id"] == "adr")
        other = next(v for v in data["validators"] if v["validator_id"] == "quality")
        assert target["criteria"][-2:] == [
            "The org_id is derived server-side from the API key lookup, never accepted from the request.",
            "Every D1 query carries an org filter.",
        ]
        assert other["criteria"] == ["tests pass"]
        assert verify_protocol(Protocol(**data)).passed

    def test_accepting_adds_a_missing_criteria_list(self, tmp_path, monkeypatch):
        import yaml

        protocol_path = _make_repo(tmp_path, {"001-rule.md": RULE_RECORD})
        text = protocol_path.read_text().replace(
            "  criteria:\n  - The Architecture Decision Records in docs/adr apply here\n", ""
        )
        protocol_path.write_text(text)
        monkeypatch.chdir(tmp_path)

        rc = intake_cmd.intake_command(_args(accept_all=True, validator="adr"))

        assert rc == 0
        data = yaml.safe_load(protocol_path.read_text())
        target = next(v for v in data["validators"] if v["validator_id"] == "adr")
        assert target["criteria"] == [
            "The org_id is derived server-side from the API key lookup, never accepted from the request."
        ]

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

    def test_repeat_intake_skips_existing_criteria_and_does_not_rewrite(self, tmp_path, monkeypatch, capsys):
        protocol_path = _make_repo(tmp_path, {"001-rule.md": RULE_RECORD})
        monkeypatch.chdir(tmp_path)
        assert intake_cmd.intake_command(_args(accept_all=True)) == 0
        before = protocol_path.read_bytes()

        rc = intake_cmd.intake_command(_args(accept_all=True))

        output = capsys.readouterr().out
        assert rc == 0
        assert "Skipped 1 criterion(s) already in the protocol" in output
        assert "Nothing written" in output
        assert protocol_path.read_bytes() == before

    def test_criterion_in_other_validator_is_not_offered(self, tmp_path, monkeypatch, capsys):
        import yaml
        protocol_path = _make_repo(tmp_path, {"001-rule.md": RULE_RECORD})
        data = yaml.safe_load(protocol_path.read_text())
        quality = next(v for v in data["validators"] if v["validator_id"] == "quality")
        quality["criteria"].append("The org_id is derived server-side from the API key lookup, never accepted from the request.")
        protocol_path.write_text(yaml.safe_dump(data, sort_keys=False))
        before = protocol_path.read_bytes()
        monkeypatch.chdir(tmp_path)

        rc = intake_cmd.intake_command(_args(accept_all=True, validator="adr"))

        assert rc == 0
        output = capsys.readouterr().out
        assert "Skipped 1 criterion(s) already in the protocol" in output
        assert "Wrote 0" not in output
        assert protocol_path.read_bytes() == before

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


def test_generate_adr_reviews_and_writes_only_accepted(tmp_path, monkeypatch, capsys):
    from snodo.survey.adr_proposals import ADRProposal, EvidenceReference
    _make_repo(tmp_path, {"README.md": "# Decisions\n"})
    for name in ("a.py", "b.py", "c.py"):
        (tmp_path / name).write_text("pattern")
    monkeypatch.chdir(tmp_path)
    proposal = ADRProposal("Use a shared client", "Context", "Use shared client", "Benefits",
                           tuple(EvidenceReference(f"{name}.py") for name in "abc"))
    monkeypatch.setattr("snodo.survey.adr_analysis.analyze_adr_proposals",
                        lambda root: ([proposal], [], None))
    rc = intake_cmd.intake_command(_args(generate_adr=True), ask=lambda prompt: "y")
    record = tmp_path / "docs/decisions/001-use-a-shared-client.md"
    assert rc == 0
    assert "Proposed" in record.read_text()
    assert "decision: Use shared client" in capsys.readouterr().out


def test_generate_adr_json_and_reject_do_not_write(tmp_path, monkeypatch, capsys):
    from snodo.survey.adr_proposals import ADRProposal, EvidenceReference
    _make_repo(tmp_path, {"README.md": "# Decisions\n"})
    (tmp_path / "a.py").write_text("a")
    monkeypatch.chdir(tmp_path)
    proposal = ADRProposal("A decision", "Context", "Decision", "Consequences",
                           (EvidenceReference("a.py"),))
    monkeypatch.setattr("snodo.survey.adr_analysis.analyze_adr_proposals",
                        lambda root: ([proposal], [], None))
    assert intake_cmd.intake_command(_args(generate_adr=True, json=True)) == 0
    assert json.loads(capsys.readouterr().out)["written"] is False
    assert not list((tmp_path / "docs/decisions").glob("[0-9]*.md"))
    assert intake_cmd.intake_command(_args(generate_adr=True, reject_all=True)) == 0
    assert not list((tmp_path / "docs/decisions").glob("[0-9]*.md"))


def test_generate_adr_without_decision_folder_explains_setup(tmp_path, monkeypatch, capsys):
    from git import Repo
    Repo.init(str(tmp_path))
    monkeypatch.chdir(tmp_path)
    rc = intake_cmd.intake_command(_args(generate_adr=True))
    output = capsys.readouterr().out
    assert rc == 0
    assert "docs/decisions" in output and "README" in output and "Nygard" in output


def test_generate_adr_accept_all_and_no_input_and_analysis_error(tmp_path, monkeypatch, capsys):
    from snodo.survey.adr_proposals import ADRProposal, EvidenceReference
    _make_repo(tmp_path, {"README.md": "# Decisions\n"})
    monkeypatch.chdir(tmp_path)
    proposal = ADRProposal("Shared client", "Context", "Use it", "Benefits",
                           (EvidenceReference("x.py"),))
    monkeypatch.setattr("snodo.survey.adr_analysis.analyze_adr_proposals",
                        lambda root: ([proposal], [], None))
    assert intake_cmd.intake_command(_args(generate_adr=True, accept_all=True, no_input=True)) == 0
    assert len(list((tmp_path / "docs/decisions").glob("[0-9]*.md"))) == 1
    (tmp_path / "docs/decisions/001-shared-client.md").unlink()
    monkeypatch.setattr("snodo.survey.adr_analysis.analyze_adr_proposals",
                        lambda root: ([], [], "model unavailable"))
    assert intake_cmd.intake_command(_args(generate_adr=True)) == 4
    assert "model unavailable" in capsys.readouterr().err
    assert not list((tmp_path / "docs/decisions").glob("[0-9]*.md"))

"""Tests for readiness assessment engine (snodo/readiness/checker.py).

FILE: tests/engine/test_readiness.py (Fixes #216)

PROVES:
- Readiness is a property of method scaffolding relative to the protocol.
- Architecture validator with no decision records reports findings and a low figure.
- Fully satisfied protocol declarations report zero findings and a 100% full figure.
- Decision records present on disk in working tree but uncommitted in git HEAD are
  reported as missing from branch / uncommitted.
- Unset environment variable appears in workstation report without altering the
  repository readiness figure.
- Findings are ordered by cheapest fix at highest severity first.
- Whole protocol is assessed regardless of active mode, with demanding modes tagged.
"""

import subprocess
from pathlib import Path
import pytest

from snodo.compiler.models import (
    DisagreementPolicy,
    Mode,
    Protocol,
    Validator,
)
from snodo.readiness.checker import assess_readiness
from snodo.readiness.models import FindingSeverity, ReadinessKind


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Create a clean git repository fixture."""
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    (root / "README.md").write_text("# Test Repo\n")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=root, check=True)
    return root


def _make_protocol(
    validators=None,
    modes=None,
    protocol_id="test-proto",
) -> Protocol:
    if validators is None:
        validators = [
            Validator(
                validator_id="val_quality",
                validator_type="quality",
                tooling={"test_command": "pytest"},
            )
        ]
    if modes is None:
        modes = [
            Mode(
                mode_id="build",
                name="Build Mode",
                validators=[v.validator_id for v in validators],
            )
        ]
    return Protocol(
        protocol_id=protocol_id,
        name="Test Protocol",
        version="1.0.0",
        modes=modes,
        validators=validators,
        disagreement_policy=DisagreementPolicy.UNANIMOUS,
        initial_mode=modes[0].mode_id,
        roles=[],
    )


def test_fully_satisfied_protocol_scores_full_figure(git_repo: Path):
    """A protocol whose method declarations are all committed and satisfied scores 100%."""
    # 1. Commit .snodo/protocol.yml
    snodo_dir = git_repo / ".snodo"
    snodo_dir.mkdir()
    (snodo_dir / "protocol.yml").write_text("protocol_id: test-proto\n")
    subprocess.run(["git", "add", ".snodo/protocol.yml"], cwd=git_repo, check=True)

    # 2. Commit a decision record
    dec_dir = git_repo / "docs" / "decisions"
    dec_dir.mkdir(parents=True)
    (dec_dir / "001-init.md").write_text("# 001 Initial Decision\n")
    subprocess.run(["git", "add", "docs/decisions/001-init.md"], cwd=git_repo, check=True)

    # 3. Commit a cited path
    (git_repo / "docs" / "architecture.md").write_text("# Architecture\n")
    subprocess.run(["git", "add", "docs/architecture.md"], cwd=git_repo, check=True)

    subprocess.run(["git", "commit", "-qm", "scaffolding ready"], cwd=git_repo, check=True)

    protocol = _make_protocol(
        validators=[
            Validator(
                validator_id="val_arch",
                validator_type="architecture",
                criteria=["Check docs/decisions/", "Adhere to docs/architecture.md"],
            ),
            Validator(
                validator_id="val_quality",
                validator_type="quality",
                tooling={"test_command": "pytest"},
            ),
        ],
        modes=[
            Mode(mode_id="design", name="Design", validators=["val_arch"]),
            Mode(mode_id="build", name="Build", validators=["val_arch", "val_quality"]),
        ],
    )

    assessment = assess_readiness(git_repo, protocol)

    assert assessment.score == 100
    assert assessment.passed_checks == assessment.total_checks
    assert len(assessment.repository_findings) == 0


def test_architecture_validator_with_no_decision_records_scores_low(git_repo: Path):
    """Architecture validator declared with no decision records reports findings and low score."""
    protocol = _make_protocol(
        validators=[
            Validator(
                validator_id="val_arch",
                validator_type="architecture",
                criteria=["Follow recorded decisions"],
            )
        ],
        modes=[
            Mode(mode_id="plan", name="Plan", validators=["val_arch"]),
        ],
    )

    assessment = assess_readiness(git_repo, protocol)

    # Should report missing decision records finding
    assert assessment.score < 100
    assert len(assessment.repository_findings) >= 1

    arch_findings = [f for f in assessment.repository_findings if "architecture_decisions" in f.id]
    assert len(arch_findings) == 1
    assert arch_findings[0].severity == FindingSeverity.BLOCKER
    assert "val_arch" in arch_findings[0].description
    assert "plan" in arch_findings[0].modes


def test_uncommitted_decision_record_reported_as_missing_from_head(git_repo: Path):
    """Decision records present on disk but uncommitted in git HEAD are flagged as uncommitted."""
    # Create docs/decisions/001-adr.md on disk, but do NOT git add / git commit it
    dec_dir = git_repo / "docs" / "decisions"
    dec_dir.mkdir(parents=True)
    (dec_dir / "001-adr.md").write_text("# Decision\n")

    protocol = _make_protocol(
        validators=[
            Validator(
                validator_id="val_arch",
                validator_type="architecture",
                criteria=["Check docs/decisions/"],
            )
        ]
    )

    assessment = assess_readiness(git_repo, protocol)

    # Must flag that decision record is uncommitted in HEAD
    uncommitted = [f for f in assessment.repository_findings if "uncommitted" in f.id and "architecture" in f.id]
    assert len(uncommitted) == 1
    assert uncommitted[0].severity == FindingSeverity.BLOCKER
    assert uncommitted[0].fix_cost == 1  # Trivial git add fix
    assert "uncommitted in git HEAD" in uncommitted[0].description


def test_unset_environment_variable_reported_without_altering_score(git_repo: Path, monkeypatch):
    """Unset API credentials appear in workstation report but do not move repository readiness score."""
    # Commit required scaffolding
    snodo_dir = git_repo / ".snodo"
    snodo_dir.mkdir()
    (snodo_dir / "protocol.yml").write_text("protocol_id: test-proto\n")
    subprocess.run(["git", "add", ".snodo/protocol.yml"], cwd=git_repo, check=True)
    subprocess.run(["git", "commit", "-qm", "commit proto"], cwd=git_repo, check=True)

    protocol = _make_protocol(
        validators=[
            Validator(
                validator_id="val_quality",
                validator_type="quality",
                tooling={"test_command": "pytest"},
                model="claude-3-7-sonnet",
            )
        ]
    )

    # Ensure ANTHROPIC_API_KEY is unset
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    assessment = assess_readiness(git_repo, protocol)

    # Repository score remains 100% because all git-level scaffolding is satisfied
    assert assessment.score == 100
    assert len(assessment.repository_findings) == 0

    # Workstation findings carry the missing credential
    cred_findings = [f for f in assessment.workstation_findings if f.id.startswith("credential_missing")]
    assert len(cred_findings) == 1
    assert cred_findings[0].kind == ReadinessKind.WORKSTATION
    assert "ANTHROPIC_API_KEY" in cred_findings[0].description


def test_findings_ordering_cheapest_fix_at_highest_severity_first(git_repo: Path):
    """Findings are ordered: blocker (cost 1 then 2) before warn (cost 1 then 2)."""
    # 1. Uncommitted protocol -> warn, cost 1
    snodo_dir = git_repo / ".snodo"
    snodo_dir.mkdir()
    (snodo_dir / "protocol.yml").write_text("protocol_id: test-proto\n")

    # 2. Decision record on disk uncommitted -> blocker, cost 1
    dec_dir = git_repo / "docs" / "decisions"
    dec_dir.mkdir(parents=True)
    (dec_dir / "001-adr.md").write_text("# Decision\n")

    # 3. Missing cited path -> warn, cost 2
    protocol = _make_protocol(
        validators=[
            Validator(
                validator_id="val_arch",
                validator_type="architecture",
                criteria=["docs/decisions/", "Check missing/spec.md"],
            ),
            Validator(
                validator_id="val_quality",
                validator_type="quality",
                # No tooling.test_command and no marker file -> blocker, cost 2
                tooling={},
            ),
        ]
    )

    assessment = assess_readiness(git_repo, protocol)
    findings = assessment.all_findings

    # Expected order:
    # 1. blocker with cost 1 (architecture_decisions_uncommitted)
    # 2. blocker with cost 2 (quality_test_command_unresolvable)
    # 3. warn with cost 1 (protocol_uncommitted)
    # 4. warn with cost 2 (cited_path_missing)

    severities = [f.severity for f in findings]
    # All blockers must appear before all warns
    first_warn_idx = next(i for i, s in enumerate(severities) if s == FindingSeverity.WARN)
    blockers = findings[:first_warn_idx]
    warns = findings[first_warn_idx:]

    assert all(f.severity == FindingSeverity.BLOCKER for f in blockers)
    assert all(f.severity == FindingSeverity.WARN for f in warns)

    # Within blockers: cost 1 <= cost 2
    assert blockers[0].fix_cost <= blockers[-1].fix_cost


def test_readiness_assesses_whole_protocol_across_all_modes(git_repo: Path):
    """Checks derive from all modes in the protocol and tag the demanding modes."""
    protocol = _make_protocol(
        validators=[
            Validator(validator_id="val_plan", validator_type="architecture"),
            Validator(validator_id="val_quality", validator_type="quality", tooling={"test_command": "pytest"}),
        ],
        modes=[
            Mode(mode_id="plan_mode", name="Plan Mode", validators=["val_plan"]),
            Mode(mode_id="code_mode", name="Code Mode", validators=["val_quality"]),
        ],
    )

    assessment = assess_readiness(git_repo, protocol)

    # The architecture finding should specifically cite 'plan_mode'
    plan_findings = [f for f in assessment.repository_findings if "architecture" in f.id]
    assert len(plan_findings) == 1
    assert "plan_mode" in plan_findings[0].modes

    # Filtering for 'code_mode' should exclude the plan_mode architecture finding
    code_mode_findings = assessment.findings_for_mode("code_mode")
    assert not any("architecture" in f.id for f in code_mode_findings)

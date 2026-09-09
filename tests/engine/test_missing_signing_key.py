"""The engine refuses to build a graph when no signing key is present.

FILE: tests/engine/test_missing_signing_key.py

The RS256 public key at ~/.ssh/NO-AGENT/snodo.pub.pem is a hard requirement
of GraphBuilder construction (engine/loop.py): the engine verifies
DecisionRecords and must never run without a verification key.  The suite's
isolate_home fixture seeds a keypair into an isolated HOME, so this file
points HOME at a deliberately EMPTY directory to pin the refusal itself —
both the module-level load and the engine-level construction — with the
documented "Run 'snodo init'" guidance.  The key requirement must not be
weakened away to make tests pass; it is enforced here.
"""

import pytest
from snodo.compiler.models import Mode, Protocol, Validator


def _minimal_protocol() -> Protocol:
    return Protocol(
        protocol_id="nokey",
        name="No Key Protocol",
        modes=[Mode(mode_id="producer", name="Producer", tools=[], validators=[])],
        validators=[
            Validator(validator_id="v1", validator_type="security",
                      evaluation_phase="pre_execute"),
        ],
        initial_mode="producer",
    )


@pytest.fixture
def home_without_signing_keys(tmp_path, monkeypatch):
    """Point HOME at a fresh empty directory: no ~/.ssh/NO-AGENT anywhere."""
    empty_home = tmp_path / "home-no-keys"
    empty_home.mkdir()
    monkeypatch.setenv("HOME", str(empty_home))
    monkeypatch.setenv("USERPROFILE", str(empty_home))
    return empty_home


def test_load_public_key_refuses_without_key(home_without_signing_keys):
    """load_public_key raises FileNotFoundError with the documented guidance."""
    from snodo.infrastructure.signing_keys import load_public_key

    with pytest.raises(FileNotFoundError) as excinfo:
        load_public_key()

    message = str(excinfo.value)
    assert str(home_without_signing_keys / ".ssh" / "NO-AGENT" / "snodo.pub.pem") in message
    assert "snodo init" in message
    # The refusal is a refusal to read a key that is not there — it must not
    # have created the directory or any key file.
    assert not (home_without_signing_keys / ".ssh").exists()


def test_graph_builder_refuses_without_key(home_without_signing_keys):
    """GraphBuilder refuses to construct (no graph, no run) without a signing key."""
    from snodo.engine.loop import GraphBuilder

    with pytest.raises(FileNotFoundError, match="Public key not found"):
        GraphBuilder(_minimal_protocol())


def test_engine_run_reports_missing_key_refusal(home_without_signing_keys, tmp_path, monkeypatch, capsys):
    """The plan-run path surfaces the refusal as 'Failed to build graph' + exit 1.

    A missing key must halt execution with the documented message, not degrade
    into an unsigned, unverifiable run.
    """
    import json

    import yaml
    from types import SimpleNamespace

    from snodo.cli.commands.plan_run import _run_plan
    from snodo.mcp.planner import PlannerMCP

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / ".snodo").mkdir()
    (project_dir / ".snodo" / "protocol.yml").write_text(
        """
protocol_id: "nokey_p"
name: "No Key Plan Protocol"
version: "1.0.0"
initial_mode: "producer"
modes:
  - mode_id: "producer"
    name: "Producer"
    tools: ["edit"]
    validators: ["quality"]
validators:
  - validator_id: "quality"
    validator_type: "quality"
    criteria: ["Pass quality"]
disagreement_policy: "unanimous"
""".strip()
    )

    planner = PlannerMCP(str(project_dir))
    plan_dir = planner.plans_dir / "nokey_plan"
    wave1 = plan_dir / "wave_1"
    wave1.mkdir(parents=True)
    (plan_dir / "plan.yml").write_text(yaml.dump({
        "name": "nokey_plan",
        "intent": "Refuse without a key",
        "waves": [{"id": "1", "tasks": ["task_1_1"], "depends_on": []}],
    }))
    (plan_dir / "status.json").write_text(json.dumps({
        "plan_name": "nokey_plan",
        "tasks": {"task_1_1": {"status": "pending"}},
    }))
    (wave1 / "task_1_1_task.md").write_text("Spec for task 1.1")

    monkeypatch.chdir(project_dir)
    monkeypatch.setattr(
        "snodo.infrastructure.paths.require_project_root", lambda: str(project_dir)
    )

    args = SimpleNamespace(
        protocol=".snodo/protocol.yml",
        model=None,
        plan="nokey_plan",
        wave=None,
        mock=True,
        interactive=False,
        no_isolation=True,
    )
    result = _run_plan(args)
    assert result == 1

    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert "Failed to build graph" in output, output
    assert "Public key not found" in output, output
    assert "snodo init" in output, output

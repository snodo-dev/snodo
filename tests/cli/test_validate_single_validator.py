"""Targeted validator and model controls for ``snodo validate``."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import subprocess

import pytest

from tests.cli.test_machine_interface import _mock_validator_config


def _invoke(project, capsys, **options):
    from snodo.cli.commands.validate_cmd import validate_command

    values = dict(
        task_spec="do the thing", phase="pre_execute", protocol=".snodo/protocol.yml",
        mode=None, json=True, validator=None, model=None,
    )
    values.update(options)
    args = SimpleNamespace(**values)
    with patch("snodo.infrastructure.paths.resolve_project_root", return_value=str(project)), \
         patch("snodo.validators.runner.resolve_validator_completion",
               return_value=(MagicMock(), "configured/model", _mock_validator_config())), \
         patch("snodo.validators.llm_validator.supports_response_schema", return_value=False):
        return validate_command(args)



@pytest.fixture
def validate_project(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.co"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    (tmp_path / "README.md").write_text("test")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=tmp_path, check=True)
    state = tmp_path / ".snodo"
    state.mkdir()
    (state / "state.json").write_text(json.dumps({"current_mode": "producer"}))
    _two_validator_protocol(tmp_path)
    return tmp_path


def _two_validator_protocol(project):
    path = project / ".snodo" / "protocol.yml"
    path.write_text(
        'protocol_id: contract\nname: Contract\nversion: "1.0.0"\n'
        'modes:\n  - mode_id: producer\n    name: Producer\n'
        '    tools: [edit, dispatch]\n    validators: [security, quality]\n'
        'validators:\n  - validator_id: security\n    validator_type: security\n'
        '    criteria: ["Check security"]\n  - validator_id: quality\n'
        '    validator_type: quality\n    model: pinned/model\n    criteria: ["Check quality"]\n'
        'disagreement_policy: unanimous\ninitial_mode: producer\n'
    )


def test_validate_runs_only_the_selected_validator(validate_project, capsys):
    _two_validator_protocol(validate_project)
    with patch("snodo.validators.runner.run_validators", return_value=([], {})) as run:
        _invoke(validate_project, capsys, validator="quality")
    assert [v.validator_id for v in run.call_args.kwargs["validators"]] == ["quality"]


def test_validate_unknown_validator_lists_available_ids(validate_project, capsys):
    _two_validator_protocol(validate_project)
    result = _invoke(validate_project, capsys, validator="missing")
    assert result != 0
    payload = json.loads(capsys.readouterr().out)
    assert "security, quality" in payload["error"]


def test_validate_model_override_beats_pinned_model(validate_project, capsys):
    _two_validator_protocol(validate_project)
    builder_models = []

    def build(model, base):
        builder_models.append(model)
        return base

    with patch("snodo.validators.runner.build_completion_fn", side_effect=build):
        with patch("litellm.completion", MagicMock(), create=True):
            _invoke(validate_project, capsys, validator="quality", model="other/provider")
    assert builder_models == ["other/provider"]


def test_validate_never_touches_the_verdict_cache(validate_project, capsys):
    with patch("snodo.validators.runner.run_validators", return_value=([], {})) as run:
        _invoke(validate_project, capsys)
    assert "verdict_cache" not in run.call_args.kwargs

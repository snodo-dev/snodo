"""Branch and hosted-request validation reuse the validate machine interface."""

import json
import subprocess
from types import SimpleNamespace
from unittest.mock import patch

from snodo.cli.commands.validate_changes import validate_changes


def _project(root):
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.co"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    (root / "README.md").write_text("base")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=root, check=True)
    state = root / ".snodo"
    state.mkdir()
    (state / "protocol.yml").write_text(
        'protocol_id: contract\nname: Contract\nversion: "1.0.0"\n'
        'modes:\n  - mode_id: producer\n    name: Producer\n'
        '    tools: [edit, dispatch]\n    validators: [security]\n'
        'validators:\n  - validator_id: security\n'
        '    validator_type: security\n    criteria: ["Check security"]\n'
        'disagreement_policy: unanimous\ninitial_mode: producer\n'
    )
    (state / "state.json").write_text(json.dumps({"current_mode": "producer"}))
    (root / "README.md").write_text("head")
    subprocess.run(["git", "commit", "-qam", "head"], cwd=root, check=True)
    return subprocess.check_output(["git", "rev-parse", "HEAD~"], cwd=root, text=True).strip(), \
        subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()


def test_ref_range_runs_existing_validator_and_returns_pass(tmp_path, capsys):
    base, head = _project(tmp_path)
    from tests.cli.test_machine_interface import _completion_fn, _mock_validator_config
    with patch("snodo.infrastructure.paths.resolve_project_root", return_value=str(tmp_path)), \
         patch("snodo.validators.runner.resolve_validator_completion", return_value=(_completion_fn("pass"), "mock-model", _mock_validator_config())), \
         patch("snodo.validators.llm_validator.supports_response_schema", return_value=False):
        result = validate_changes(task_spec="", phase="pre_execute", protocol=".snodo/protocol.yml", mode=None,
                                  json_output=True, base=base, head=head, change_request=None)
    assert result == 0
    assert '"status": "pass"' in capsys.readouterr().out


def test_blocking_verdict_uses_documented_exit_and_payload(tmp_path, capsys):
    from tests.cli.test_machine_interface import _completion_fn, _mock_validator_config
    project = tmp_path
    base, head = _project(project)
    with patch("snodo.infrastructure.paths.resolve_project_root", return_value=str(project)), \
         patch("snodo.validators.runner.resolve_validator_completion", return_value=(_completion_fn("blocker"), "mock-model", _mock_validator_config())), \
         patch("snodo.validators.llm_validator.supports_response_schema", return_value=False):
        assert validate_changes(task_spec="", phase="pre_execute", protocol=".snodo/protocol.yml", mode=None,
                                json_output=True, base=base, head=head, change_request=None) == 1
    import json
    result_payload = json.loads(capsys.readouterr().out)
    assert result_payload["status"] == "blocker"
    assert result_payload["halt"]["status"] == "blocker"


def test_pull_request_number_uses_provider_resolved_range(tmp_path, capsys):
    from tests.cli.test_machine_interface import _completion_fn, _mock_validator_config

    project = tmp_path
    base, head = _project(project)
    provider = SimpleNamespace(resolve_change_request_refs=lambda number: (base, head))
    with patch("snodo.infrastructure.paths.resolve_project_root", return_value=str(project)), \
         patch("snodo.providers.registry.detect_provider", return_value=provider), \
         patch("snodo.validators.runner.resolve_validator_completion", return_value=(_completion_fn("pass"), "mock-model", _mock_validator_config())), \
         patch("snodo.validators.llm_validator.supports_response_schema", return_value=False):
        assert validate_changes(task_spec="", phase="pre_execute", protocol=".snodo/protocol.yml", mode=None,
                                json_output=True, base=None, head=None, change_request=17) == 0
    assert '"status": "pass"' in capsys.readouterr().out

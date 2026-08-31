"""Tests for AGYAdapter (Antigravity CLI in-place coder adapter).

FILE: tests/coders/test_agy_adapter.py (Fixes #145)

PROVES:
- AGYAdapter builds expected argv with model pass-through and bare model stripping
- Missing agy binary raises LLMCallError naming tool and install instructions
- Subprocess non-zero exit surfaces agy's stderr in LLMCallError
- --coder agy selects AGYAdapter while validator model remains unaffected
- Mutating protected .snodo/ paths raises SnodoMutationError
"""

import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
import pytest

from snodo.coders import get_coder, resolve_coder_name
from snodo.coders.agy_adapter import AGYAdapter
from snodo.coders.base import LLMCallError, SnodoMutationError
from snodo.compiler.models import Mode, Protocol, Validator
from snodo.core.interfaces import TaskSpec
from snodo.engine.loop import GraphBuilder


@pytest.fixture
def temp_workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    (root / "README.md").write_text("# Test Workspace\n")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=root, check=True)
    return root


def test_agy_adapter_argv_construction(temp_workspace: Path):
    """AGYAdapter constructs the expected subprocess argv."""
    adapter = AGYAdapter(model="agy/Gemini 3.5 Flash", workspace=temp_workspace)
    spec = TaskSpec(description="Implement feature X", constraints=[])

    executed_argv = []

    def fake_run(argv, **kwargs):
        nonlocal executed_argv
        executed_argv = argv
        (temp_workspace / "feature.py").write_text("def x(): pass\n")
        return SimpleNamespace(returncode=0, stdout="Success", stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run):
        adapter.implement(spec)

    assert executed_argv[0] == "agy"
    assert executed_argv[1] == "-p"
    assert "Implement feature X" in executed_argv[2]
    assert "--dangerously-skip-permissions" in executed_argv
    assert "--add-dir" in executed_argv
    dir_idx = executed_argv.index("--add-dir")
    assert executed_argv[dir_idx + 1] == str(temp_workspace)
    assert "--model" in executed_argv
    model_idx = executed_argv.index("--model")
    assert executed_argv[model_idx + 1] == "Gemini 3.5 Flash"


def test_agy_binary_missing_raises_actionable_error(temp_workspace: Path):
    """Missing agy binary raises LLMCallError naming tool and install url."""
    adapter = AGYAdapter(workspace=temp_workspace)
    spec = TaskSpec(description="Implement feature X", constraints=[])

    with mock.patch("subprocess.run", side_effect=FileNotFoundError):
        with pytest.raises(LLMCallError) as exc_info:
            adapter.implement(spec)

    msg = str(exc_info.value)
    assert "agy not found on PATH" in msg
    assert "https://antigravity.google/docs/cli" in msg


def test_agy_nonzero_exit_surfaces_stderr(temp_workspace: Path):
    """Non-zero returncode surfaces stderr in LLMCallError."""
    adapter = AGYAdapter(workspace=temp_workspace)
    spec = TaskSpec(description="Implement feature X", constraints=[])

    def fake_run(argv, **kwargs):
        return SimpleNamespace(returncode=1, stdout="", stderr="Error: invalid model specified")

    with mock.patch("subprocess.run", side_effect=fake_run):
        with pytest.raises(LLMCallError) as exc_info:
            adapter.implement(spec)

    msg = str(exc_info.value)
    assert "agy run failed (rc=1)" in msg
    assert "Error: invalid model specified" in msg


def test_coder_selection_and_validator_model_unaffected():
    """--coder agy selects AGYAdapter while validator model is unaffected."""
    protocol = Protocol(
        protocol_id="test",
        name="Test",
        version="1.0.0",
        initial_mode="p",
        modes=[Mode(mode_id="p", name="P", tools=["edit"])],
        validators=[Validator(validator_id="v1", validator_type="security")],
        disagreement_policy="unanimous",
    )

    coder_name = resolve_coder_name(model="gpt-4o", cli_coder="agy")
    assert coder_name == "agy"

    coder = get_coder(coder_name, model="gpt-4o")
    assert isinstance(coder, AGYAdapter)

    with mock.patch("snodo.config.ConfigManager.load", return_value={"model": "gpt-4o"}):
        builder = GraphBuilder(protocol=protocol, coder=coder)
        # Validator model remains gpt-4o (unaffected by choice of agy coder)
        assert builder._validator_runner._default_model == "gpt-4o"
        assert getattr(coder, "model") == "gpt-4o"


def test_snodo_mutation_raises_error(temp_workspace: Path):
    """Mutating protected .snodo/ paths raises SnodoMutationError."""
    adapter = AGYAdapter(workspace=temp_workspace)
    spec = TaskSpec(description="Implement feature X", constraints=[])

    def fake_run_mutating_snodo(argv, **kwargs):
        snodo_dir = temp_workspace / ".snodo"
        snodo_dir.mkdir(exist_ok=True)
        (snodo_dir / "illegal_edit.txt").write_text("hack")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run_mutating_snodo):
        with pytest.raises(SnodoMutationError) as exc_info:
            adapter.implement(spec)

    assert ".snodo/illegal_edit.txt" in str(exc_info.value)


# ---------------------------------------------------------------------------
# A model string only reaches the CLI when it names THIS adapter's namespace.
#
# `snodo run -m X` names the model that JUDGES the work — it is resolved
# through litellm for the validators and the classifier. An external coding
# agent owns its own catalog and cannot route it:
#
#   agy run failed (rc=1): invalid model selection
#   (--model "deepseek/deepseek-v4-flash"): not recognized as a known model
#
# So a non-namespaced model must be dropped, leaving the tool on its own
# default.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "coder_name,namespaced,bare,flag",
    [
        ("agy", "agy/Gemini 3.6 Flash (Medium)", "Gemini 3.6 Flash (Medium)", "--model"),
        ("opencode-cli", "opencode-cli/deepseek/deepseek-chat", "deepseek/deepseek-chat", "-m"),
    ],
)
def test_namespaced_model_is_forwarded_to_the_cli(
    coder_name, namespaced, bare, flag, temp_workspace: Path,
):
    """A model named in the adapter's own namespace reaches --model or -m, unprefixed."""
    coder = get_coder(coder_name, model=namespaced, workspace=temp_workspace)
    argv = coder._build_argv("do the thing", str(temp_workspace), coder._bare_model())

    assert flag in argv
    assert argv[argv.index(flag) + 1] == bare


@pytest.mark.parametrize("coder_name,flag", [("agy", "--model"), ("opencode-cli", "-m")])
@pytest.mark.parametrize(
    "validator_model",
    ["deepseek/deepseek-v4-flash", "ollama/deepseek-v4-flash", "claude-sonnet-4-20250514"],
)
def test_validator_model_is_not_forwarded_to_the_cli(
    coder_name, flag, validator_model, temp_workspace: Path,
):
    """A model that is not in this adapter's namespace never reaches the model flag.

    This is the regression: -m sets the judging model, and forwarding it made
    the external agent reject the run before writing anything.
    """
    coder = get_coder(coder_name, model=validator_model, workspace=temp_workspace)

    assert coder._bare_model() == ""
    argv = coder._build_argv("do the thing", str(temp_workspace), coder._bare_model())
    assert flag not in argv
    assert validator_model not in argv


@pytest.mark.parametrize("coder_name,flag", [("agy", "--model"), ("opencode-cli", "-m")])
def test_no_model_at_all_omits_the_flag(coder_name, flag, temp_workspace: Path):
    """With no model given, the CLI is left on its own last-selected default."""
    coder = get_coder(coder_name, workspace=temp_workspace)

    assert coder._bare_model() == ""
    argv = coder._build_argv("x", str(temp_workspace), coder._bare_model())
    assert flag not in argv


def test_halt_payload_coder_and_judging_model_attribution(temp_workspace: Path):
    """Halt payload attributes coder, coder_model, and judging_model correctly."""
    from snodo.engine.state import LoopState
    from snodo.core.interfaces import Task

    protocol = Protocol(
        protocol_id="test",
        name="Test",
        version="1.0.0",
        initial_mode="p",
        modes=[Mode(mode_id="p", name="P", tools=["edit"])],
        validators=[Validator(validator_id="v1", validator_type="security")],
        disagreement_policy="unanimous",
    )
    task = Task(id="t1", spec="spec")

    # 1. External CLI coder with judging model (coder_model should be None, judging_model should be specified model)
    coder = get_coder("agy", model="deepseek/deepseek-v4-flash", workspace=temp_workspace)
    builder = GraphBuilder(protocol=protocol, coder=coder)
    builder._default_model = "deepseek/deepseek-v4-flash"
    state = LoopState(task=task, current_mode="p", is_blocked=True, halt_type="blocked")

    payload = builder._build_halt_payload(state)
    assert payload["coder"] == "agy"
    assert payload["coder_model"] is None
    assert payload["judging_model"] == "deepseek/deepseek-v4-flash"

    # 2. External CLI coder with explicit namespaced model (coder_model should be knowable, e.g. "Gemini 3.5 Flash")
    coder_namespaced = get_coder("agy", model="agy/Gemini 3.5 Flash", workspace=temp_workspace)
    builder_namespaced = GraphBuilder(protocol=protocol, coder=coder_namespaced)
    builder_namespaced._default_model = "gpt-4o"
    payload_namespaced = builder_namespaced._build_halt_payload(state)
    assert payload_namespaced["coder"] == "agy"
    assert payload_namespaced["coder_model"] == "Gemini 3.5 Flash"
    assert payload_namespaced["judging_model"] == "gpt-4o"

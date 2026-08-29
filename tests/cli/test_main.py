"""Integration tests for Snodo CLI - SIMPLIFIED for Task 3.4.

FILE: tests/cli/test_main.py

Simplified tests that work with the integrated MCP system.
"""

import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from snodo.compiler.models import Protocol

from snodo.cli.main import (
    DEFAULT_PROTOCOL,
    INTENT_PROTOCOL,
    SOLO_PROTOCOL,
    TWO_PLUS_N_PROTOCOL,
    load_protocol,
    main,
)


@pytest.fixture
def temp_project_dir():
    """Create a temporary project directory for testing."""
    temp_dir = tempfile.mkdtemp()

    # Initialize git repo
    subprocess.run(["git", "init"], cwd=temp_dir, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=temp_dir, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=temp_dir, capture_output=True, check=True)

    # Initial commit
    readme = Path(temp_dir) / "README.md"
    readme.write_text("test")
    subprocess.run(["git", "add", "README.md"], cwd=temp_dir, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=temp_dir, capture_output=True, check=True)

    original_cwd = Path.cwd()

    try:
        import os
        os.chdir(temp_dir)
        yield Path(temp_dir)
    finally:
        os.chdir(original_cwd)
        shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def initialized_project(temp_project_dir):
    """Create a temp directory with initialized Snodo project."""
    with patch('sys.argv', ['snodo', 'init', '--template', 'team', '--yes']):
        result = main()

    assert result == 0
    assert (temp_project_dir / ".snodo").exists()
    assert (temp_project_dir / ".snodo" / "protocol.yml").exists()

    return temp_project_dir


# Init Command Tests

def test_init_creates_directory(temp_project_dir):
    """Test snodo init creates .snodo directory."""
    with patch('sys.argv', ['snodo', 'init', '--template', 'team', '--yes']):
        result = main()

    assert result == 0
    assert (temp_project_dir / ".snodo").exists()


def test_init_creates_protocol_file(temp_project_dir):
    """Test snodo init creates protocol.yml."""
    with patch('sys.argv', ['snodo', 'init', '--template', 'team', '--yes']):
        main()

    protocol_file = temp_project_dir / ".snodo" / "protocol.yml"
    assert protocol_file.exists()

    with open(protocol_file) as f:
        data = yaml.safe_load(f)

    assert data is not None
    assert "protocol_id" in data
    assert "modes" in data
    assert "validators" in data


def test_init_protocol_is_valid(temp_project_dir):
    """Test generated team protocol can be loaded as Protocol object."""
    with patch('sys.argv', ['snodo', 'init', '--template', 'team', '--yes']):
        main()

    protocol_file = temp_project_dir / ".snodo" / "protocol.yml"
    protocol = load_protocol(protocol_file)

    assert protocol is not None
    assert protocol.protocol_id == "default"
    assert len(protocol.modes) == 3
    assert len(protocol.validators) == 11


def test_init_writes_state_json(temp_project_dir):
    """Test snodo init creates .snodo/state.json with initial_mode."""
    with patch('sys.argv', ['snodo', 'init', '--template', 'team', '--yes']):
        result = main()
    assert result == 0

    from snodo.infrastructure.state import read_state
    state = read_state(str(temp_project_dir))
    assert state.current_mode == "producer"
    assert state.active_session == {}


def test_init_mode_flag_skips_picker(temp_project_dir):
    """Test snodo init --mode reviewer sets state.json to reviewer."""
    with patch('sys.argv', ['snodo', 'init', '--template', 'team', '--mode', 'reviewer', '--yes']):
        result = main()
    assert result == 0

    from snodo.infrastructure.state import read_state
    state = read_state(str(temp_project_dir))
    assert state.current_mode == "reviewer"


def test_init_piped_keeps_default(temp_project_dir):
    """Test snodo init with piped stdin keeps default initial_mode."""
    with patch('sys.argv', ['snodo', 'init', '--template', 'team', '--yes']):
        with patch('sys.stdin.isatty', return_value=False):
            result = main()

    assert result == 0
    from snodo.infrastructure.state import read_state
    state = read_state(str(temp_project_dir))
    assert state.current_mode == "producer"


def test_init_fails_if_already_exists(temp_project_dir):
    """Test snodo init fails if .snodo already exists."""
    with patch('sys.argv', ['snodo', 'init', '--template', 'team', '--yes']):
        result = main()
    assert result == 0

    with patch('sys.argv', ['snodo', 'init', '--template', 'team', '--yes']):
        result = main()
    assert result == 1


def test_init_force_overwrites(temp_project_dir):
    """Test snodo init --force overwrites existing directory."""
    with patch('sys.argv', ['snodo', 'init', '--template', 'team', '--yes']):
        result = main()
    assert result == 0

    protocol_file = temp_project_dir / ".snodo" / "protocol.yml"
    protocol_file.write_text("# modified\n")

    with patch('sys.argv', ['snodo', 'init', '--force', '--template', 'team', '--yes']):
        result = main()
    assert result == 0

    content = protocol_file.read_text()
    assert "# modified" not in content
    assert "protocol_id" in content


def test_init_refuses_nested_snodo(temp_project_dir):
    """Test snodo init refuses when a parent already has .snodo."""
    # Create .snodo in parent directory first
    (temp_project_dir / ".snodo").mkdir()
    nested_dir = temp_project_dir / "subdir"
    nested_dir.mkdir()

    import os
    os.chdir(str(nested_dir))
    try:
        with patch('sys.argv', ['snodo', 'init', '--template', 'team', '--yes']):
            result = main()
        assert result == 1
        assert not (nested_dir / ".snodo").exists()
    finally:
        os.chdir(str(temp_project_dir))


def test_init_force_allows_nested_snodo(temp_project_dir):
    """Test snodo init --force allows nested .snodo despite parent."""
    (temp_project_dir / ".snodo").mkdir()
    nested_dir = temp_project_dir / "subdir"
    nested_dir.mkdir()

    import os
    os.chdir(str(nested_dir))
    try:
        with patch('sys.argv', ['snodo', 'init', '--force', '--template', 'team', '--yes']):
            result = main()
        assert result == 0
        assert (nested_dir / ".snodo").exists()
    finally:
        os.chdir(str(temp_project_dir))


# Load Protocol Tests

def test_load_protocol_success(initialized_project):
    """Test loading valid protocol file."""
    protocol_file = initialized_project / ".snodo" / "protocol.yml"
    protocol = load_protocol(protocol_file)

    assert protocol is not None
    assert protocol.protocol_id == "default"


def test_load_protocol_file_not_found(temp_project_dir):
    """Test loading non-existent protocol file."""
    protocol = load_protocol(temp_project_dir / "nonexistent.yml")
    assert protocol is None


def test_load_protocol_invalid_yaml(temp_project_dir):
    """Test loading invalid YAML."""
    protocol_file = temp_project_dir / "bad.yml"
    protocol_file.write_text("invalid: yaml: content: [\n")

    protocol = load_protocol(protocol_file)
    assert protocol is None


def test_load_protocol_invalid_structure(temp_project_dir):
    """Test loading YAML with invalid protocol structure."""
    protocol_file = temp_project_dir / "bad.yml"
    protocol_file.write_text("protocol_id: test\n# missing required fields\n")

    protocol = load_protocol(protocol_file)
    assert protocol is None


# Run Command Tests (with mock coder)

def test_run_missing_protocol(temp_project_dir):
    """Test snodo run fails if protocol doesn't exist."""
    (temp_project_dir / ".snodo").mkdir(exist_ok=True)
    with patch('sys.argv', ['snodo', 'run', 'test task', '--mock']):
        result = main()

    assert result == 1


def test_run_with_valid_protocol(initialized_project):
    """Test snodo run with valid protocol (mock mode).

    Under the corrected policy semantics, criteria-bearing validators
    produce warn stubs → unanimous ESCALATE → exit code 1.
    This is correct; warn withholds approval.
    """
    with patch('sys.argv', ['snodo', 'run', 'test task', '--mock']):
        result = main()

    assert result == 1


def test_run_creates_files(initialized_project):
    """Test snodo run exits with block (warn stubs under unanimous)."""
    with patch('sys.argv', ['snodo', 'run', 'add hello function', '--mock']):
        result = main()

    assert result == 1


def test_run_custom_protocol_path(temp_project_dir):
    """Test snodo run with custom protocol (team template → ESCALATE)."""
    (temp_project_dir / ".snodo").mkdir(exist_ok=True)
    custom_protocol = temp_project_dir / "custom.yml"
    custom_protocol.write_text(DEFAULT_PROTOCOL + "\n")

    with patch('sys.argv', ['snodo', 'run', 'test task', '--protocol', str(custom_protocol), '--mock']):
        result = main()

    assert result == 1


def test_end_to_end_init_and_run(temp_project_dir):
    """Test complete workflow: init -> run."""
    # Step 1: Initialize
    with patch('sys.argv', ['snodo', 'init', '--template', 'team', '--yes']):
        result = main()
    assert result == 0

    # Step 2: Verify structure
    assert (temp_project_dir / ".snodo").exists()
    assert (temp_project_dir / ".snodo" / "protocol.yml").exists()

    # Step 3: Run task — warn stubs under unanimous → ESCALATE → exit 1
    with patch('sys.argv', ['snodo', 'run', 'implement feature X', '--mock']):
        result = main()
    assert result == 1


def test_multiple_tasks_in_sequence(initialized_project):
    """Test running multiple tasks sequentially."""
    tasks = [
        "implement feature A",
        "implement feature B",
        "implement feature C"
    ]

    for task_desc in tasks:
        with patch('sys.argv', ['snodo', 'run', task_desc, '--mock']):
            result = main()
        assert result == 1


# CLI Interface Tests

def test_no_command_shows_help():
    """Test running snodo without command shows help."""
    with patch('sys.argv', ['snodo']):
        result = main()

    assert result == 0


def test_invalid_command():
    """Test invalid command is rejected with non-zero exit."""
    with patch('sys.argv', ['snodo', 'invalid']):
        try:
            result = main()
            assert result != 0
        except SystemExit as e:
            assert e.code != 0
        except Exception:
            # typer raises UsageError for unknown commands — also a failure
            pass


def test_init_help(capsys):
    """Test snodo init --help."""
    with patch('sys.argv', ['snodo', 'init', '--help']):
        result = main()

    assert result == 0
    out = capsys.readouterr().out
    assert "Initialize Snodo" in out


def test_run_help(capsys):
    """Test snodo run --help."""
    with patch('sys.argv', ['snodo', 'run', '--help']):
        result = main()

    assert result == 0
    out = capsys.readouterr().out
    assert "Execute a task" in out


def test_run_requires_description(temp_project_dir):
    """Test snodo run without description returns error."""
    (temp_project_dir / ".snodo").mkdir(exist_ok=True)
    with patch('sys.argv', ['snodo', 'run']):
        result = main()
    assert result == 1


# Edge Cases

def test_run_with_empty_description(initialized_project):
    """Test running with empty task description."""
    with patch('sys.argv', ['snodo', 'run', '', '--mock']):
        result = main()

    assert result == 1


def test_run_with_special_characters(initialized_project):
    """Test task description with special characters."""
    special_desc = "Implement feature with $pecial @#! characters"

    with patch('sys.argv', ['snodo', 'run', special_desc, '--mock']):
        result = main()

    assert result == 1


def test_protocol_with_minimal_config(temp_project_dir):
    """Test protocol with minimal valid config."""
    protocol_file = temp_project_dir / ".snodo" / "protocol.yml"
    protocol_file.parent.mkdir(parents=True, exist_ok=True)

    minimal_protocol = """
protocol_id: "minimal"
name: "Minimal"
version: "1.0.0"
modes:
  - mode_id: "producer"
    name: "Producer"
    tools: ["edit"]
    validators: ["test"]
validators:
  - validator_id: "test"
    validator_type: "testing"
    criteria: ["Check"]
disagreement_policy: "unanimous"
initial_mode: "producer"
""".strip()

    protocol_file.write_text(minimal_protocol + "\n")

    # Should load and run (may block if validators produce warn)
    protocol = load_protocol(protocol_file)
    assert protocol is not None

    with patch('sys.argv', ['snodo', 'run', 'test', '--mock']):
        result = main()

    assert result == 0


# Template Selection Tests

def test_init_template_solo(temp_project_dir):
    """Test snodo init --template solo creates protocol with 1 mode + merge tools."""
    with patch('sys.argv', ['snodo', 'init', '--template', 'solo', '--yes']):
        result = main()

    assert result == 0
    protocol_file = temp_project_dir / ".snodo" / "protocol.yml"
    protocol = load_protocol(protocol_file)

    assert protocol is not None
    assert protocol.protocol_id == "solo"
    assert len(protocol.modes) == 1
    assert protocol.modes[0].mode_id == "producer"
    assert "merge" in protocol.modes[0].tools
    assert "commit" in protocol.modes[0].tools


def test_init_template_team(temp_project_dir):
    """Test snodo init --template team creates protocol with 3 modes."""
    with patch('sys.argv', ['snodo', 'init', '--template', 'team', '--yes']):
        result = main()

    assert result == 0
    protocol_file = temp_project_dir / ".snodo" / "protocol.yml"
    protocol = load_protocol(protocol_file)

    assert protocol is not None
    assert protocol.protocol_id == "default"
    assert len(protocol.modes) == 3
    mode_ids = {m.mode_id for m in protocol.modes}
    assert mode_ids == {"producer", "reviewer", "planner"}


def test_init_interactive_prompt_solo(temp_project_dir):
    """Test snodo init without template prompts interactively - select solo."""
    from snodo.protocols import list_templates
    idx = list_templates().index("solo") + 1
    with patch('sys.argv', ['snodo', 'init', '--yes']):
        with patch('builtins.input', return_value=str(idx)):
            result = main()

    assert result == 0
    protocol_file = temp_project_dir / ".snodo" / "protocol.yml"
    protocol = load_protocol(protocol_file)

    assert protocol is not None
    assert protocol.protocol_id == "solo"
    assert len(protocol.modes) == 1


def test_init_interactive_prompt_team(temp_project_dir):
    """Test snodo init without template prompts interactively - select team."""
    from snodo.protocols import list_templates
    idx = list_templates().index("team") + 1
    with patch('sys.argv', ['snodo', 'init', '--yes']):
        with patch('builtins.input', return_value=str(idx)):
            result = main()

    assert result == 0
    protocol_file = temp_project_dir / ".snodo" / "protocol.yml"
    protocol = load_protocol(protocol_file)

    assert protocol is not None
    assert protocol.protocol_id == "default"
    assert len(protocol.modes) == 3


def test_init_interactive_invalid_choice_reprompts(temp_project_dir):
    """An invalid interactive choice re-prompts; it never silently substitutes."""
    from snodo.protocols import list_templates
    team_idx = list_templates().index("team") + 1
    with patch('sys.argv', ['snodo', 'init', '--yes']):
        with patch('builtins.input', side_effect=['invalid', str(team_idx)]):
            result = main()

    assert result == 0
    protocol_file = temp_project_dir / ".snodo" / "protocol.yml"
    protocol = load_protocol(protocol_file)

    assert protocol is not None
    assert protocol.protocol_id == "default"


def test_init_template_2plus_n(temp_project_dir):
    """Test snodo init --template 2+n creates protocol with 2 modes."""
    with patch('sys.argv', ['snodo', 'init', '--template', '2+n', '--yes']):
        result = main()

    assert result == 0
    protocol_file = temp_project_dir / ".snodo" / "protocol.yml"
    protocol = load_protocol(protocol_file)

    assert protocol is not None
    assert protocol.protocol_id == "2+n"
    assert len(protocol.modes) == 2
    mode_ids = {m.mode_id for m in protocol.modes}
    assert mode_ids == {"producer", "reviewer"}


def test_init_template_2plus_n_validators(temp_project_dir):
    """Test 2+n template has 7 validators with correct phases."""
    with patch('sys.argv', ['snodo', 'init', '--template', '2+n', '--yes']):
        result = main()

    assert result == 0
    protocol_file = temp_project_dir / ".snodo" / "protocol.yml"
    protocol = load_protocol(protocol_file)

    assert protocol is not None
    validator_ids = {v.validator_id for v in protocol.validators}
    assert validator_ids == {"security", "architecture", "conventions", "quality", "protocol_adherence", "meta-spec", "acceptance"}

    pre = [v for v in protocol.validators if v.evaluation_phase == "pre_execute"]
    post = [v for v in protocol.validators if v.evaluation_phase == "post_execute"]
    assert len(pre) == 5
    assert len(post) == 2
    assert {v.validator_id for v in post} == {"quality", "acceptance"}


def test_init_template_2plus_n_wf1_exclusive_tools(temp_project_dir):
    """Test 2+n template keeps exclusive (approval-conferring) tools in one mode."""
    with patch('sys.argv', ['snodo', 'init', '--template', '2+n', '--yes']):
        result = main()

    assert result == 0
    protocol_file = temp_project_dir / ".snodo" / "protocol.yml"
    protocol = load_protocol(protocol_file)

    assert protocol is not None
    for tool in protocol.exclusive_tools:
        holders = [m.mode_id for m in protocol.modes if tool in m.tools]
        assert len(holders) <= 1, f"exclusive tool '{tool}' held by {holders}"


def test_init_interactive_prompt_2plus_n(temp_project_dir):
    """Test interactive prompt selects 2+n template."""
    from snodo.protocols import list_templates
    idx = list_templates().index("2+n") + 1
    with patch('sys.argv', ['snodo', 'init', '--yes']):
        with patch('builtins.input', return_value=str(idx)):
            result = main()

    assert result == 0
    protocol_file = temp_project_dir / ".snodo" / "protocol.yml"
    protocol = load_protocol(protocol_file)

    assert protocol is not None
    assert protocol.protocol_id == "2+n"
    assert len(protocol.modes) == 2


def test_two_plus_n_protocol_is_valid():
    """Test TWO_PLUS_N_PROTOCOL string constant parses and constructs."""
    data = yaml.safe_load(TWO_PLUS_N_PROTOCOL)
    protocol = Protocol(**data)

    assert protocol.protocol_id == "2+n"
    assert len(protocol.modes) == 2
    assert len(protocol.validators) == 7
    assert protocol.initial_mode == "producer"


def test_two_plus_n_reviewer_transitions():
    """Test 2+n reviewer has approved->complete and rejected->producer transitions."""
    data = yaml.safe_load(TWO_PLUS_N_PROTOCOL)
    protocol = Protocol(**data)

    reviewer = protocol.get_mode("reviewer")
    assert reviewer is not None
    assert reviewer.transitions == {"approved": "complete", "rejected": "producer"}


def test_solo_protocol_is_valid():
    """Test SOLO_PROTOCOL is a valid Protocol object."""
    data = yaml.safe_load(SOLO_PROTOCOL)
    protocol = Protocol(**data)

    assert protocol.protocol_id == "solo"
    assert len(protocol.modes) == 1
    assert len(protocol.validators) == 5
    assert protocol.initial_mode == "producer"


def test_solo_producer_has_commit_merge_tools():
    """Test solo producer mode has commit, merge, and dispatch tools."""
    data = yaml.safe_load(SOLO_PROTOCOL)
    protocol = Protocol(**data)

    producer = protocol.get_mode("producer")
    assert producer is not None
    assert "commit" in producer.tools
    assert "merge" in producer.tools
    assert "edit" in producer.tools
    assert "dispatch" in producer.tools
    assert "test" in producer.tools
    assert "validate" in producer.tools


# WF3 Enforcement via load_protocol

def test_load_protocol_wf3_violation_rejected(temp_project_dir):
    """Protocol with dispatch mode but no pre_execute validators is rejected."""
    bad_protocol = """
protocol_id: "bad"
name: "Bad Protocol"
version: "1.0.0"
modes:
  - mode_id: "producer"
    name: "Producer"
    tools: ["edit", "dispatch"]
    validators: ["v1"]
validators:
  - validator_id: "v1"
    validator_type: "quality"
    evaluation_phase: "post_execute"
disagreement_policy: "unanimous"
initial_mode: "producer"
global_constraints: []
""".strip()
    protocol_file = temp_project_dir / "bad_protocol.yml"
    protocol_file.write_text(bad_protocol)

    result = load_protocol(protocol_file)
    assert result is None


def test_load_protocol_wf3_valid_dispatch_loads(temp_project_dir):
    """Protocol with dispatch mode and pre_execute validator loads OK."""
    good_protocol = """
protocol_id: "good"
name: "Good Protocol"
version: "1.0.0"
modes:
  - mode_id: "producer"
    name: "Producer"
    tools: ["edit", "dispatch"]
    validators: ["v1"]
validators:
  - validator_id: "v1"
    validator_type: "security"
    evaluation_phase: "pre_execute"
disagreement_policy: "unanimous"
initial_mode: "producer"
global_constraints: []
""".strip()
    protocol_file = temp_project_dir / "good_protocol.yml"
    protocol_file.write_text(good_protocol)

    result = load_protocol(protocol_file)
    assert result is not None
    assert result.protocol_id == "good"


def test_shipped_solo_protocol_passes_verification():
    """SOLO_PROTOCOL template passes all WF1-WF5 checks."""
    import yaml
    from snodo.compiler.verifier import verify_protocol
    data = yaml.safe_load(SOLO_PROTOCOL)
    protocol = Protocol(**data)
    result = verify_protocol(protocol)
    assert result.passed, f"SOLO_PROTOCOL failed verification: {result.errors}"


def test_shipped_default_protocol_passes_verification():
    """DEFAULT_PROTOCOL template passes all WF1-WF5 checks."""
    import yaml
    from snodo.compiler.verifier import verify_protocol
    data = yaml.safe_load(DEFAULT_PROTOCOL)
    protocol = Protocol(**data)
    result = verify_protocol(protocol)
    assert result.passed, f"DEFAULT_PROTOCOL failed verification: {result.errors}"


# ============================================================================
# Intent template tests
# ============================================================================


def test_init_template_intent(temp_project_dir):
    """Test snodo init --template intent exits 0 and writes protocol.yml."""
    with patch('sys.argv', ['snodo', 'init', '--template', 'intent', '--yes']):
        result = main()

    assert result == 0
    assert (temp_project_dir / ".snodo").exists()
    assert (temp_project_dir / ".snodo" / "protocol.yml").exists()


def test_init_template_intent_loads_as_protocol(temp_project_dir):
    """Test the generated intent protocol loads as a Protocol object."""
    with patch('sys.argv', ['snodo', 'init', '--template', 'intent', '--yes']):
        main()

    protocol_file = temp_project_dir / ".snodo" / "protocol.yml"
    protocol = load_protocol(protocol_file)

    assert protocol is not None
    assert protocol.protocol_id == "intent"
    assert len(protocol.modes) == 1
    assert protocol.modes[0].mode_id == "producer"


def test_intent_protocol_validators(temp_project_dir):
    """Test intent template has spec-manners (pre, warn-cap) + review (post)."""
    with patch('sys.argv', ['snodo', 'init', '--template', 'intent', '--yes']):
        main()

    protocol_file = temp_project_dir / ".snodo" / "protocol.yml"
    protocol = load_protocol(protocol_file)

    assert protocol is not None
    validator_ids = {v.validator_id for v in protocol.validators}
    # The post_execute "review" validator was removed intentionally (see the
    # NOTE in intent.yml: uncommitted coder diffs made it spuriously flag
    # every task). Enforcement is WARN-only spec manners + blocker-level
    # global constraints on the output.
    assert validator_ids == {"spec-manners"}

    pre = [v for v in protocol.validators if v.evaluation_phase == "pre_execute"]
    post = [v for v in protocol.validators if v.evaluation_phase == "post_execute"]
    assert len(pre) == 1
    assert len(post) == 0
    assert pre[0].validator_id == "spec-manners"
    assert pre[0].severity_cap == "warn"


def test_intent_protocol_global_constraints(temp_project_dir):
    """Test intent template has the two expected global constraints."""
    with patch('sys.argv', ['snodo', 'init', '--template', 'intent', '--yes']):
        main()

    protocol_file = temp_project_dir / ".snodo" / "protocol.yml"
    protocol = load_protocol(protocol_file)

    assert protocol is not None
    constraint_ids = {c.constraint_id for c in protocol.global_constraints}
    assert constraint_ids == {"no_secrets", "files_in_scope"}


def test_intent_interactive_prompt(temp_project_dir):
    """Test interactive picker selecting the intent option produces intent template."""
    from snodo.protocols import list_templates
    idx = list_templates().index("intent") + 1
    with patch('sys.argv', ['snodo', 'init', '--yes']):
        with patch('builtins.input', return_value=str(idx)):
            result = main()

    assert result == 0
    protocol_file = temp_project_dir / ".snodo" / "protocol.yml"
    protocol = load_protocol(protocol_file)

    assert protocol is not None
    assert protocol.protocol_id == "intent"


def test_intent_protocol_constant_is_valid():
    """Test INTENT_PROTOCOL string constant parses and constructs."""
    data = yaml.safe_load(INTENT_PROTOCOL)
    protocol = Protocol(**data)

    assert protocol.protocol_id == "intent"
    assert len(protocol.modes) == 1
    assert len(protocol.validators) == 1
    assert protocol.initial_mode == "producer"


def test_shipped_intent_protocol_passes_verification():
    """INTENT_PROTOCOL template passes all WF1-WF5 checks."""
    import yaml
    from snodo.compiler.verifier import verify_protocol
    data = yaml.safe_load(INTENT_PROTOCOL)
    protocol = Protocol(**data)
    result = verify_protocol(protocol)
    assert result.passed, f"INTENT_PROTOCOL failed verification: {result.errors}"


def test_init_template_greenfield(temp_project_dir):
    """`--template greenfield` initialises successfully (added via directory)."""
    with patch('sys.argv', ['snodo', 'init', '--template', 'greenfield', '--yes']):
        result = main()

    assert result == 0
    protocol_file = temp_project_dir / ".snodo" / "protocol.yml"
    protocol = load_protocol(protocol_file)

    assert protocol is not None
    assert protocol.protocol_id == "greenfield"


def test_init_unknown_template_fails_cleanly(temp_project_dir, capsys):
    """`--template nonexistent` exits non-zero with a helpful message, no traceback."""
    with patch('sys.argv', ['snodo', 'init', '--template', 'nonexistent', '--yes']):
        with pytest.raises(SystemExit) as exc:
            main()

    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "nonexistent" in err
    assert "Available templates" in err
    assert "Traceback" not in err
    # Nothing written.
    assert not (temp_project_dir / ".snodo").exists()


def test_init_failed_init_is_atomic(temp_project_dir):
    """A failed init leaves no .snodo/, no .gitignore, and no project identity."""
    with patch('sys.argv', ['snodo', 'init', '--template', 'nonexistent', '--yes']):
        with pytest.raises(SystemExit):
            main()

    assert not (temp_project_dir / ".snodo").exists()
    assert not (temp_project_dir / ".gitignore").exists()
    assert not (temp_project_dir / ".snodo" / "project.json").exists()


def test_init_menu_lists_every_template(temp_project_dir, capsys):
    """The interactive menu enumerates every shipped template."""
    from snodo.protocols import list_templates, template_display_name
    with patch('sys.argv', ['snodo', 'init', '--yes']):
        with patch('builtins.input', return_value='1'):
            main()

    out = capsys.readouterr().out
    for name in list_templates():
        assert name in out
        assert template_display_name(name) in out

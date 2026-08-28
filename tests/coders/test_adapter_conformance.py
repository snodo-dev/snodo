"""Conformance: every registered coder adapter's change is observable,
attributable, and reviewable through the same channel.

An adapter produces a :class:`CodeArtifact` (channel A), but post-execute
validators review the committed diff ``git diff base_ref..HEAD`` (channel B —
the "## Code Change" block in ``llm_validator``/``acceptance``), where
``base_ref`` is the execute-node HEAD anchor captured before the coder runs
(Fixes #103). These two channels diverged: ``OpenCodeAdapter`` wrote to the
volume-mounted workspace in place and never committed, so HEAD did not move
and ``HEAD~1..HEAD`` resolved to the PREVIOUS commit — validators confidently
reviewed the wrong change and passed.

The seam is implicit (an ABC, two ``skip_*`` booleans, ``hasattr`` duck
typing), so an adapter that lacks the "commit what I wrote" capability is
indistinguishable from one where the capability does not exist. This test
pins the invariant that both channels describe the same change for EVERY
registered adapter, so a future adapter that writes in place without leaving
its change reviewable fails at the branch instead of surfacing months later.

For in-process adapters (litellm, mock, openai, anthropic, gemini) the
engine writes and commits the artifact (``ExecutorMixin``); for in-place
adapters (opencode, opencode-cli) the adapter writes in place and the
``InPlaceCoderAdapter`` base class owns the commit. The assertion below runs
after the same write+commit step the engine performs, so the two families
are compared on equal footing.
"""

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from snodo.coders import CODER_REGISTRY
from snodo.coders.mock import MockModelResponse
from snodo.core.interfaces import Task, TaskSpec
from snodo.engine.nodes.executor import ExecutorMixin
from snodo.tools.git import GitMCP
from snodo.tools.workspace import WorkspaceMCP


# ========== fixture helpers ==========

def _git_workspace(tmp_path: Path) -> Path:
    """A throwaway git repo with one initial commit and a configured identity."""
    root = tmp_path / "proj"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=root, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=root, check=True, capture_output=True,
    )
    (root / "README.md").write_text("# Initial")
    subprocess.run(["git", "add", "."], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=root, check=True, capture_output=True)
    return root


class _ExecutorHarness(ExecutorMixin):
    """Minimal holder for the real engine write+commit methods."""

    def __init__(self):
        self._job_id = ""
        self._session_id = ""
        self._progress = None
        self._worktree_path = None
        self._worktree_degraded = False

    def _audit(self, *args, **kwargs):
        return None


# ========== per-adapter drivers ==========

_FEATURE_PATH = "src/feature.py"
_FEATURE_CONTENT = "def feature():\n    return 42\n"


def _drive_llm_adapter(name: str, workspace: Path, spec: TaskSpec):
    """In-process LLM-backed adapters: return a CodeArtifact, never write."""
    cls = CODER_REGISTRY[name]
    coder = cls(model="gpt-4")
    content = json.dumps([
        {"path": _FEATURE_PATH, "content": _FEATURE_CONTENT, "action": "write"},
    ])
    coder._completion_fn = lambda **kw: MockModelResponse(content)
    artifact = coder.implement(spec)
    return coder, artifact


def _drive_mock_adapter(name: str, workspace: Path, spec: TaskSpec):
    from snodo.coders.mock import MockAdapter
    coder = MockAdapter()
    artifact = coder.implement(spec)
    return coder, artifact


def _drive_opencode_adapter(name: str, workspace: Path, spec: TaskSpec):
    """Container/HTTP path: writes in place via the volume mount, never commits."""
    from snodo.coders.opencode_adapter import OpenCodeAdapter
    coder = OpenCodeAdapter(model="opencode/deepseek/deepseek-chat", workspace=workspace)
    container = mock.Mock()
    container.is_running.return_value = True
    container.base_url = "http://localhost:55440"
    coder._container = container

    def fake_wait(session_id, spec):
        # Simulate the container editing the volume-mounted workspace in place.
        (workspace / "src").mkdir(parents=True, exist_ok=True)
        (workspace / _FEATURE_PATH).write_text(_FEATURE_CONTENT)

    coder._create_session = lambda: "sess-conformance"
    coder._wait_for_completion = fake_wait
    coder._cleanup_session = lambda session_id: None
    artifact = coder.implement(spec)
    return coder, artifact


def _drive_opencode_cli_adapter(name: str, workspace: Path, spec: TaskSpec):
    """Host-CLI path: shells ``opencode run``, which writes in place."""
    from snodo.coders.opencode_cli_adapter import OpenCodeCLIAdapter
    coder = OpenCodeCLIAdapter(model="opencode-cli/deepseek/deepseek-chat", workspace=workspace)

    def fake_run(cmd, **kwargs):
        (workspace / "src").mkdir(parents=True, exist_ok=True)
        (workspace / _FEATURE_PATH).write_text(_FEATURE_CONTENT)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run):
        artifact = coder.implement(spec)
    return coder, artifact


_DRIVERS = {
    "litellm": _drive_llm_adapter,
    "openai": _drive_llm_adapter,
    "anthropic": _drive_llm_adapter,
    "gemini": _drive_llm_adapter,
    "mock": _drive_mock_adapter,
    "opencode": _drive_opencode_adapter,
    "opencode-cli": _drive_opencode_cli_adapter,
}


@pytest.mark.parametrize("name", sorted(CODER_REGISTRY))
def test_change_is_observable_attributable_and_reviewable(name, tmp_path):
    """Every registered adapter's change is reviewable through the channel
    post-execute validators actually read (``base_ref..HEAD``, where
    ``base_ref`` is the execute-node HEAD anchor from Fixes #103)."""
    workspace = _git_workspace(tmp_path)

    # The execute node captures the HEAD anchor BEFORE the coder runs
    # (Fixes #103). In-place adapters write and commit inside implement(), so
    # the anchor must be taken before the driver.
    git_mcp = GitMCP(str(workspace))
    base_ref = git_mcp.get_head_sha()

    driver = _DRIVERS.get(name)
    assert driver is not None, (
        f"{name} is registered but has no conformance driver — a registered "
        "adapter must be covered here or the conformance gate is a hole."
    )

    spec = TaskSpec(description="add a feature", constraints=[])
    coder, artifact = driver(name, workspace, spec)

    # Channel A — observable: the adapter reports what it changed.
    assert artifact.files, f"{name}: implement() returned no artifacts"

    # Engine-equivalent write + commit, exactly the path _default_executor uses.
    harness = _ExecutorHarness()
    workspace_mcp = WorkspaceMCP(str(workspace))
    task = Task(id="task_conformance", spec="add a feature")
    paths = harness._apply_file_operations(workspace_mcp, coder, artifact, task)
    harness._commit_artifacts(git_mcp, coder, paths, task)

    # Channel A — attributable: the reported paths are real, on-disk changes
    # after the write the engine (or the in-place coder) performed.
    for f in artifact.files:
        if f.action != "delete":
            assert (workspace / f.path).exists(), (
                f"{name}: artifact {f.path} is not present in the workspace"
            )

    # Channel B — reviewable: post-execute validators read git diff
    # base_ref..HEAD (llm_validator.py / acceptance.py "## Code Change"). It
    # must be THIS change, not the previous commit and not empty.
    diff = git_mcp.diff_between_refs(base_ref, "HEAD")
    assert diff.strip(), (
        f"{name}: {base_ref}..HEAD is empty after execution — post-execute "
        "validators are blind. An in-place adapter must leave its change "
        "committed (InPlaceCoderAdapter._commit_changes owns this)."
    )
    for f in artifact.files:
        assert f.path in diff, (
            f"{name}: artifact {f.path} is absent from the diff post-execute "
            "validators review — the two channels disagree."
        )


@pytest.mark.parametrize("name", sorted(CODER_REGISTRY))
def test_commit_not_happening_is_refused(name, tmp_path):
    """An adapter that produces file operations but whose commit does not
    happen is REFUSED with head_not_moved — not merely left with an empty
    diff (Fixes #109). This is the case that occurred in production and that
    #103 exists to catch: HEAD~1..HEAD would resolve to the previous commit
    and the judges would pass."""
    from snodo.compiler.models import Protocol, Mode, Validator, DisagreementPolicy
    from snodo.core.interfaces import ValidatorResult
    from snodo.engine.loop import GraphBuilder
    from snodo.infrastructure.tokens import TokenIssuer
    from tests.conftest import TEST_SECRET

    workspace = _git_workspace(tmp_path)
    driver = _DRIVERS.get(name)
    assert driver is not None, (
        f"{name} is registered but has no conformance driver — a registered "
        "adapter must be covered here or the conformance gate is a hole."
    )

    spec = TaskSpec(description="add a feature", constraints=[])
    coder, artifact = driver(name, workspace, spec)
    assert artifact.files, f"{name}: implement() returned no artifacts"

    protocol = Protocol(
        protocol_id="conformance_no_commit",
        name="Conformance No Commit",
        version="1.0.0",
        modes=[
            Mode(
                mode_id="producer",
                name="Producer",
                tools=["edit"],
                validators=["security", "acceptance"],
            )
        ],
        validators=[
            Validator(
                validator_id="security",
                validator_type="security",
                criteria=["Check the change"],
                evaluation_phase="pre_execute",
            ),
            Validator(
                validator_id="acceptance",
                validator_type="acceptance",
                evaluation_phase="post_execute",
                severity_cap="warn",
                tools=["read_file", "list_files", "read_diff_between_refs"],
                criteria=["Judge the produced artifacts"],
            ),
        ],
        disagreement_policy=DisagreementPolicy.UNANIMOUS,
        initial_mode="producer",
    )

    workspace_mcp = WorkspaceMCP(str(workspace))
    git_mcp = GitMCP(str(workspace))

    # The commit does not happen: for in-process adapters the engine owns the
    # commit (skip_engine_commit False), for in-place adapters the base class
    # owns it (skip_engine_commit True). Disable whichever path applies.
    def _no_commit(git_mcp, coder, artifact_paths, task):
        return []

    def _all_pass(task, validators, shell_mcp, current_mode="", **kwargs):
        return [
            ValidatorResult(validator_id=v.validator_id, severity="pass",
                            justification="ok")
            for v in validators
        ]

    harness = _ExecutorHarness()
    with mock.patch.object(harness, "_commit_artifacts", side_effect=_no_commit):
        builder = GraphBuilder(
            protocol,
            workspace_mcp=workspace_mcp,
            git_mcp=git_mcp,
            shell_mcp=None,
            executor_fn=harness._default_executor,
            validator_fn=_all_pass,
            token_issuer=TokenIssuer(secret=TEST_SECRET, ttl_seconds=3600),
        )
        graph = builder.build_graph().compile()
        result = graph.invoke({
            "task": {"id": "task_conformance", "spec": "add a feature"},
            "current_mode": "producer",
            "iteration": 0,
            "stage": "execute",
            "validation_results": [],
            "validation_token": {"jwt": "valid_token"},
            "artifacts": [],
            "constraints_passed": True,
            "constraint_violations": [],
            "policy_decision": None,
            "is_complete": False,
            "is_blocked": False,
            "metadata": {},
            "messages": [],
            "summary": "",
        })

    assert result["is_blocked"] is True, (
        f"{name}: a run whose commit did not happen was NOT refused. The "
        "post-execute judges would review the previous commit and pass — the "
        "exact seam #103 exists to catch."
    )
    assert result["halt_type"] == "head_not_moved", (
        f"{name}: expected head_not_moved halt, got {result['halt_type']}"
    )


@pytest.mark.parametrize("name", sorted(CODER_REGISTRY))
def test_declared_capabilities_are_present_on_every_adapter(name):
    """Every registered adapter carries the DECLARED optional interface.

    The engine injects capabilities unconditionally, never behind a hasattr
    guard (#68): workspace_mcp, progress_callback, _job_id, _task_id, model,
    and the two behavioural switches. A registered adapter must expose them
    (via its own attributes or inherited base-class defaults) so that "this
    adapter does not support X" is a visible fact — a missing attribute here
    means the engine would silently skip the adapter, which is the exact
    divergence this interface exists to make observable.
    """
    for expected in (
        "workspace_mcp",
        "progress_callback",
        "_job_id",
        "_task_id",
        "model",
        "skip_workspace_write",
        "skip_engine_commit",
    ):
        assert hasattr(CODER_REGISTRY[name](), expected), (
            f"{name} lacks declared coder capability '{expected}'. The engine "
            "assigns these unconditionally; a missing attribute silently "
            "disconnects the capability (docs/architecture/coder-adapter-"
            "contract.md §3.1, #68)."
        )


def test_progress_callback_is_injected_unconditionally():
    """The engine sets progress_callback unconditionally, even on adapters
    that never emit progress — the absence is never silent (#68)."""
    from snodo.coders.mock import MockAdapter

    harness = _ExecutorHarness()

    def _progress(msg):
        return None

    harness._progress = _progress
    harness._job_id = "job_x"
    harness._session_id = ""
    coder = MockAdapter()
    harness._prepare_coder(coder, None, Task(id="t_x", spec="s"))

    assert coder.progress_callback is _progress
    assert coder._job_id == "job_x"
    assert coder._task_id == "t_x"

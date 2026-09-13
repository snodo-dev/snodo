"""A halt payload records every attempt a task took (Fixes #271).

The final validator results say what happened last; the attempts say how the
task got there. A first-time pass and a hard-won pass must be distinguishable
from the payload alone, including the in-place abstention re-judges that spawn
no subtask and dispatch no coder (Fixes #268).
"""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

from snodo.compiler.models import (
    DisagreementPolicy,
    Mode,
    Protocol,
    Validator,
)
from snodo.core.interfaces import CodeArtifact, Task, ValidatorResult
from snodo.engine.closure import run_to_closure
from snodo.engine.loop import GraphBuilder
from snodo.engine.nodes.writeback import (
    _MAX_ATTEMPT_HISTORY,
    _build_attempt_summary,
)
from snodo.engine.state import LoopState
from snodo.infrastructure.tokens import TokenIssuer
from snodo.infrastructure.worktree import create_worktree
from snodo.tools.git import GitMCP
from snodo.tools.workspace import WorkspaceMCP

from tests.conftest import TEST_SECRET


def _abstention(validator_id, justification="Judge could not decide."):
    return ValidatorResult(
        validator_id=validator_id,
        severity=None,
        justification=justification,
        abstention_reason="exhausted budget after 6 turns",
    )


def _pass(validator_id):
    return ValidatorResult(validator_id=validator_id, severity="pass", justification="ok")


def _protocol(validators=None, max_recovery_depth=None):
    from snodo.compiler.models import ExecutionConfig

    execution = (
        ExecutionConfig(max_recovery_depth=max_recovery_depth)
        if max_recovery_depth is not None
        else None
    )
    kwargs = dict(
        protocol_id="attempts",
        name="Attempts",
        version="1.0.0",
        modes=[
            Mode(
                mode_id="producer",
                name="Producer",
                tools=["edit"],
                validators=[v.validator_id for v in (validators or [
                    Validator(validator_id="acceptance", validator_type="acceptance",
                              evaluation_phase="post_execute", severity_cap="warn",
                              criteria=["Judge the produced artifacts"]),
                ])],
            )
        ],
        validators=validators or [
            Validator(validator_id="acceptance", validator_type="acceptance",
                      evaluation_phase="post_execute", severity_cap="warn",
                      criteria=["Judge the produced artifacts"]),
        ],
        disagreement_policy=DisagreementPolicy.UNANIMOUS,
        initial_mode="producer",
    )
    if execution is not None:
        kwargs["execution"] = execution
    return Protocol(**kwargs)


def _post_validation(results):
    return {
        "validator_results": [
            {"validator_id": r.validator_id, "severity": r.severity,
             "justification": r.justification}
            for r in results
        ],
        "outcome": "passed",
    }


def _completed_state(results, task=None, abstention_retries=0):
    task = task or Task(id="t1", spec="Implement feature X")
    state = LoopState(task=task, current_mode="producer")
    state.is_complete = True
    state.validation_results = list(results)
    state.metadata["post_validation"] = _post_validation(results)
    state.abstention_retries = abstention_retries
    return state


class TestAttemptSummary:
    def test_first_time_pass_reports_one_attempt(self):
        """A clean pass is one attempt and zero non-verdicts."""
        builder = GraphBuilder(_protocol())
        payload = builder._build_halt_payload(_completed_state([_pass("acceptance")]))

        attempts = payload["attempts"]
        assert attempts["total"] == 1
        assert attempts["non_verdicts"] == 0
        assert attempts["coder_dispatches"] == 1
        assert attempts["history"] == [{"attempt": 1, "outcome": "passed"}]

    def test_three_abstentions_then_pass_reports_four_attempts(self):
        """The observed hard-won task: three prior attempts abstained and the
        fourth passed, each recorded with its canonical outcome."""
        prior = [
            {"attempt": 1, "validator_id": "acceptance", "severity": None,
             "justification": "no verdict"},
            {"attempt": 2, "validator_id": "acceptance", "severity": None,
             "justification": "no verdict"},
            {"attempt": 3, "validator_id": "acceptance", "severity": None,
             "justification": "no verdict"},
        ]
        task = Task(id="root_fix_3", spec="s", depth=3, prior_failures=prior)
        builder = GraphBuilder(_protocol())
        payload = builder._build_halt_payload(
            _completed_state([_pass("acceptance")], task=task)
        )

        attempts = payload["attempts"]
        assert attempts["total"] == 4
        assert attempts["non_verdicts"] == 3
        assert attempts["coder_dispatches"] == 4
        assert [e["outcome"] for e in attempts["history"]] == [
            "abstained", "abstained", "abstained", "passed",
        ]

    def test_in_place_rejudges_are_attempts_without_coder_dispatches(self):
        """The in-place abstention retries live on the loop state and are
        synthesised into the history; they are not coder dispatches."""
        builder = GraphBuilder(_protocol())
        state = _completed_state(
            [_pass("acceptance")], abstention_retries=3,
        )
        payload = builder._build_halt_payload(state)

        attempts = payload["attempts"]
        assert attempts["total"] == 4
        assert attempts["non_verdicts"] == 3
        assert attempts["coder_dispatches"] == 1
        assert [e["outcome"] for e in attempts["history"]] == [
            "abstained", "abstained", "abstained", "passed",
        ]

    def test_recovery_chain_records_each_attempts_outcome(self):
        """Prior attempts keep their own outcome: a blocker, a warning, an
        abstention, then the pass that resolved the chain."""
        prior = [
            {"attempt": 1, "validator_id": "quality", "severity": "blocker",
             "justification": "tests fail"},
            {"attempt": 2, "validator_id": "quality", "severity": "warn",
             "justification": "flaky test"},
            {"attempt": 3, "validator_id": "acceptance", "severity": None,
             "justification": "no verdict"},
        ]
        task = Task(id="root_fix_3", spec="s", depth=3, prior_failures=prior)
        builder = GraphBuilder(_protocol())
        payload = builder._build_halt_payload(
            _completed_state([_pass("acceptance")], task=task)
        )
        assert [e["outcome"] for e in payload["attempts"]["history"]] == [
            "blocked", "warned", "abstained", "passed",
        ]

    def test_history_is_bounded_but_counts_stay_exact(self):
        """A long chain cannot grow the payload without bound; the counts
        remain exact and the omitted total is reported."""
        depth = _MAX_ATTEMPT_HISTORY + 5
        prior = [
            {"attempt": n, "validator_id": "acceptance", "severity": None,
             "justification": "no verdict"}
            for n in range(1, depth + 1)
        ]
        task = Task(id="root_fix_n", spec="s", depth=depth, prior_failures=prior)
        state = _completed_state([_pass("acceptance")], task=task)
        summary = _build_attempt_summary(state, "complete")

        assert summary["total"] == depth + 1
        assert summary["non_verdicts"] == depth
        assert len(summary["history"]) == _MAX_ATTEMPT_HISTORY
        assert summary["omitted"] == depth + 1 - _MAX_ATTEMPT_HISTORY
        # The most recent entries survive, and the final pass is among them.
        assert summary["history"][-1]["attempt"] == depth + 1
        assert summary["history"][-1]["outcome"] == "passed"


class TestInPlaceRejudgeIsRecorded:
    def test_driven_rejudge_records_both_attempts(self):
        """Driving the post-execute node through an abstention and a retry
        records two attempts, one non-verdict, and no spawned subtask."""
        verdicts = iter([_abstention("acceptance"), _pass("acceptance")])

        def validator_fn(task, validators, shell_mcp, **kwargs):
            return [next(verdicts)]

        builder = GraphBuilder(_protocol(), validator_fn=validator_fn)
        loop_state = LoopState(
            task=Task(id="t1", spec="Implement feature X"),
            current_mode="producer",
            artifacts=["src/feature.py"],
        )
        built = builder._dict_to_state(builder._post_validate_node(
            builder._state_to_dict(loop_state)
        ))

        # The retry happened in place: no coder dispatch, no subtask.
        assert built.is_blocked is False
        assert built.needs_recovery is False
        assert built.spawned_subtasks == []
        assert built.abstention_retries == 1

        built.is_complete = True
        payload = builder._build_halt_payload(built)
        attempts = payload["attempts"]
        assert attempts["total"] == 2
        assert attempts["non_verdicts"] == 1
        assert attempts["coder_dispatches"] == 1
        assert attempts["history"] == [
            {"attempt": 1, "outcome": "abstained"},
            {"attempt": 2, "outcome": "passed"},
        ]


class NoOpCoder:
    """A coder that finds the work already present and writes nothing."""

    skip_workspace_write = True
    skip_engine_commit = True
    model = "mock-model"
    workspace_mcp = None
    progress_callback = None
    _job_id = ""
    _task_id = ""
    _depth = 0
    _attempt = 1

    def __init__(self):
        self.implement_calls = 0

    def implement(self, spec):
        self.implement_calls += 1
        return CodeArtifact(files=[])


def _git(*args, cwd):
    return subprocess.run(
        list(args), cwd=cwd, capture_output=True, text=True, check=True,
    )


def _init_repo(project: Path) -> None:
    _git("git", "init", "-q", "-b", "main", cwd=project)
    _git("git", "config", "user.email", "test@test.com", cwd=project)
    _git("git", "config", "user.name", "Test", cwd=project)
    (project / "README.md").write_text("# Fixture\n")
    _git("git", "add", ".", cwd=project)
    _git("git", "commit", "-qm", "initial", cwd=project)


def _task_branch_with_committed_work(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    _init_repo(project)
    wt = Path(create_worktree(str(project), "task_ab", "Implement feature X"))
    (wt / "feature.py").write_text("def feature():\n    return 42\n")
    _git("git", "add", "-A", cwd=wt)
    _git("git", "commit", "-qm", "coder: apply changes", cwd=wt)
    return str(project), str(wt)


class TestClosurePayloadRecordsAttempts:
    def test_recovery_then_pass_reports_both_attempts(self, tmp_path):
        """End to end: the root attempt warns, a recovery subtask resolves it,
        and the completing payload names both attempts."""
        protocol = Protocol(
            protocol_id="attempts_e2e",
            name="Attempts E2E",
            version="1.0.0",
            modes=[
                Mode(mode_id="producer", name="Producer", tools=["edit"],
                     validators=["security", "acceptance"])
            ],
            validators=[
                Validator(validator_id="security", validator_type="security",
                          evaluation_phase="pre_execute", criteria=["Check the change"]),
                Validator(validator_id="acceptance", validator_type="acceptance",
                          evaluation_phase="post_execute", severity_cap="warn",
                          criteria=["Judge the produced artifacts"]),
            ],
            disagreement_policy=DisagreementPolicy.UNANIMOUS,
            initial_mode="producer",
        )

        _, wt = _task_branch_with_committed_work(tmp_path)
        workspace_mcp = WorkspaceMCP(wt)
        git_mcp = GitMCP(wt)

        post_calls = []

        def validator_fn(task, validators, shell_mcp, current_mode="", **kwargs):
            if kwargs.get("phase") == "post_execute":
                post_calls.append(kwargs)
                if len(post_calls) == 1:
                    return [ValidatorResult(
                        validator_id="acceptance", severity="warn",
                        justification="criterion 2 unmet",
                    )]
                return [_pass(v.validator_id) for v in validators]
            return [_pass(v.validator_id) for v in validators]

        coder = NoOpCoder()
        audit = MagicMock()
        builder = GraphBuilder(
            protocol,
            workspace_mcp=workspace_mcp,
            git_mcp=git_mcp,
            shell_mcp=None,
            coder=coder,
            worktree_path=wt,
            validator_fn=validator_fn,
            token_issuer=TokenIssuer(secret=TEST_SECRET, ttl_seconds=3600),
            audit_log=audit,
        )

        graph = builder.build_graph().compile()
        final_state, tree = run_to_closure(
            graph, {"id": "task_ab", "spec": "Implement feature X"}, "producer",
            audit_log=audit, max_recovery_depth=3, max_total_fix_attempts=10,
        )

        assert tree.outcome == "resolved"
        # The root spawned recovery; the completing payload is the resolving
        # subtask's, which is the last halt payload persisted for the job.
        assert tree.subtasks[0].outcome == "resolved"
        payload = tree.subtasks[0].halt_payload
        attempts = payload["attempts"]
        assert attempts["total"] == 2
        assert attempts["non_verdicts"] == 0
        assert attempts["coder_dispatches"] == 2
        assert attempts["history"] == [
            {"attempt": 1, "outcome": "warned"},
            {"attempt": 2, "outcome": "passed"},
        ]

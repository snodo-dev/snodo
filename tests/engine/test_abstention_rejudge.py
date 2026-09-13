"""A post-execute abstention re-judges the judge, not the coder (Fixes #268).

An abstention is a judge reporting that it did not reach a verdict — not a
finding about the code. The recovery machinery exists to change code, so
routing an abstention there dispatches a coder at work no judge faulted and
manufactures a failure that was never diagnosed. These tests pin the corrected
behaviour:

1. a post-execute abstention on committed work re-runs the judge in place and
   never dispatches a coder;
2. repeated abstentions on unchanged code are a stall, whatever prose
   accompanies them, and halt rather than loop;
3. the retry is bounded by the protocol's per-mode max_recovery_depth, and
   `abstention_policy` keeps its meaning.
"""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

from snodo.compiler.models import (
    DisagreementPolicy,
    ExecutionConfig,
    Mode,
    Protocol,
    Validator,
)
from snodo.core.interfaces import CodeArtifact, ValidatorResult
from snodo.engine.loop import GraphBuilder, _verdict_signature
from snodo.engine.closure import run_to_closure
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


def _post_execute_protocol(abstention_policy=None, max_recovery_depth=None):
    execution = (
        ExecutionConfig(max_recovery_depth=max_recovery_depth)
        if max_recovery_depth is not None
        else None
    )
    kwargs = dict(
        protocol_id="abstention_rejudge",
        name="Abstention Re-judge",
        version="1.0.0",
        modes=[
            Mode(
                mode_id="producer",
                name="Producer",
                tools=["edit"],
                validators=["acceptance"],
            )
        ],
        validators=[
            Validator(
                validator_id="acceptance",
                validator_type="acceptance",
                evaluation_phase="post_execute",
                severity_cap="warn",
                criteria=["Judge the produced artifacts"],
            )
        ],
        disagreement_policy=DisagreementPolicy.UNANIMOUS,
        initial_mode="producer",
    )
    if abstention_policy:
        kwargs["abstention_policy"] = abstention_policy
    if execution is not None:
        kwargs["execution"] = execution
    return Protocol(**kwargs)


def _make_state(task_id="task_ab", depth=0):
    return {
        "task": {"id": task_id, "spec": "Implement feature X", "depth": depth},
        "current_mode": "producer",
        "iteration": 1,
        "stage": "validate",
        "validation_results": [],
        "validation_token": None,
        "artifacts": ["src/feature.py"],
        "constraints_passed": True,
        "constraint_violations": [],
        "policy_decision": None,
        "is_complete": False,
        "is_blocked": False,
        "halt_type": None,
        "pending_disagreement": None,
        "metadata": {},
        "messages": [],
        "summary": "",
        "spawned_subtasks": [],
        "needs_recovery": False,
    }


class TestAbstentionStallSignature:
    """An abstention repeated on unchanged code is a stall, whatever its prose."""

    def test_signature_ignores_an_abstention_justification(self):
        first = [{"validator_id": "acceptance", "severity": None,
                  "justification": "exhausted budget after 6 turns"}]
        second = [{"validator_id": "acceptance", "severity": None,
                   "justification": "judge replied without calling submit_verdict"}]
        assert _verdict_signature(first) == _verdict_signature(second)

    def test_signature_still_distinguishes_real_verdicts(self):
        warn = [{"validator_id": "acceptance", "severity": "warn",
                 "justification": "criterion 2 unmet"}]
        blocker = [{"validator_id": "acceptance", "severity": "blocker",
                    "justification": "criterion 2 unmet"}]
        assert _verdict_signature(warn) != _verdict_signature(blocker)


class TestPostExecuteAbstentionRejudges:
    """A post-execute abstention re-runs the judge; no coder recovery is spawned."""

    def test_repeated_abstentions_with_differing_prose_trip_the_stall(self):
        """The exact observed defect: three abstentions, three justifications,
        one unchanged situation. The second must be recognised as a stall."""
        justifications = iter([
            "Validator could not reach a verdict within the allocated 6 turns.",
            "Validator did not call submit_verdict after 6 turn(s). No verdict was reached.",
            "Validator could not reach a verdict within the allocated 6 turns.",
        ])
        calls = []

        def validator_fn(task, validators, shell_mcp, **kwargs):
            calls.append(kwargs.get("phase"))
            return [_abstention("acceptance", next(justifications))]

        builder = GraphBuilder(_post_execute_protocol(), validator_fn=validator_fn)
        result = builder._post_validate_node(_make_state())

        assert result["is_blocked"] is True
        assert result["halt_type"] == "abstention_stalled"
        assert result["needs_recovery"] is False
        assert result["spawned_subtasks"] == []
        # One initial judgement plus exactly one re-judge before the stall.
        assert calls == ["post_execute", "post_execute"]

    def test_abstention_then_pass_rejudges_in_place(self):
        """A judge that decides on the retry lets the task proceed — no coder."""
        verdicts = iter([_abstention("acceptance"), _pass("acceptance")])
        calls = []

        def validator_fn(task, validators, shell_mcp, **kwargs):
            calls.append(kwargs.get("phase"))
            return [next(verdicts)]

        builder = GraphBuilder(_post_execute_protocol(), validator_fn=validator_fn)
        result = builder._post_validate_node(_make_state())

        assert result["is_blocked"] is False
        assert result["needs_recovery"] is False
        assert result["spawned_subtasks"] == []
        assert result["metadata"]["post_validation"]["outcome"] == "passed"
        assert calls == ["post_execute", "post_execute"]

    def test_abstention_retry_is_bounded_by_max_recovery_depth(self):
        """A protocol that permits no recovery permits no re-judging: the
        abstention halts immediately under abstention_exhausted."""
        calls = []

        def validator_fn(task, validators, shell_mcp, **kwargs):
            calls.append(kwargs.get("phase"))
            return [_abstention("acceptance")]

        builder = GraphBuilder(
            _post_execute_protocol(max_recovery_depth=0), validator_fn=validator_fn
        )
        result = builder._post_validate_node(_make_state())

        assert result["is_blocked"] is True
        assert result["halt_type"] == "abstention_exhausted"
        assert result["spawned_subtasks"] == []
        # No re-judge at all: only the initial post-execute pass.
        assert calls == ["post_execute"]

    def test_abstention_policy_meaning_is_unchanged(self):
        """`abstention_policy` keeps deciding the outcome. Under non_blocking an
        abstention is excluded from the counts and the policy applies to the
        judges that decided — so an abstention beside a pass under `any` is a
        proceed, and no re-judge happens. The re-judge only fires when the
        policy itself reaches HALT/ESCALATE on an abstention."""
        calls = []

        def validator_fn(task, validators, shell_mcp, **kwargs):
            calls.append(kwargs.get("phase"))
            # One judge passes, the other never decides.
            return [
                _pass("acceptance"),
                _abstention("optional"),
            ]

        protocol = _post_execute_protocol(abstention_policy="non_blocking")
        protocol = protocol.model_copy(update={
            "disagreement_policy": DisagreementPolicy.ANY,
            "modes": [Mode(mode_id="producer", name="Producer", tools=["edit"],
                           validators=["acceptance", "optional"])],
            "validators": list(protocol.validators) + [
                Validator(validator_id="optional", validator_type="acceptance",
                          evaluation_phase="post_execute", severity_cap="warn",
                          criteria=["Optional judgement"]),
            ],
        })
        builder = GraphBuilder(protocol, validator_fn=validator_fn)
        result = builder._post_validate_node(_make_state())

        # The policy proceeded on the deciding judge; the abstention was
        # excluded, not re-judged, and certainly not converted to a pass.
        assert result["is_blocked"] is False
        assert result["needs_recovery"] is False
        assert calls == ["post_execute"]
        abstainer = next(
            r for r in result["validation_results"]
            if r["validator_id"] == "optional"
        )
        assert abstainer["severity"] is None

    def test_abstention_that_decides_a_warn_then_spawns_recovery(self):
        """Once a retry produces a real finding, the normal recovery path takes
        over: a warn is something a coder can act on, unlike a missing verdict."""
        verdicts = iter([
            _abstention("acceptance"),
            ValidatorResult(validator_id="acceptance", severity="warn",
                            justification="criterion 2 unmet"),
        ])
        calls = []

        def validator_fn(task, validators, shell_mcp, **kwargs):
            calls.append(kwargs.get("phase"))
            return [next(verdicts)]

        builder = GraphBuilder(_post_execute_protocol(), validator_fn=validator_fn)
        result = builder._post_validate_node(_make_state())

        assert result["is_blocked"] is False
        assert result["needs_recovery"] is True
        assert [s["id"] for s in result["spawned_subtasks"]] == ["task_ab_fix_1"]
        assert calls == ["post_execute", "post_execute"]

    def test_warn_alongside_abstention_still_spawns_recovery(self):
        """A real finding still drives recovery; only an abstention *alone* is
        retried in place. A warn is something a coder can act on."""
        def validator_fn(task, validators, shell_mcp, **kwargs):
            return [
                _abstention("acceptance"),
                ValidatorResult(validator_id="quality", severity="warn",
                                justification="tests flaky"),
            ]

        protocol = _post_execute_protocol()
        protocol = protocol.model_copy(update={
            "modes": [
                Mode(mode_id="producer", name="Producer", tools=["edit"],
                     validators=["acceptance", "quality"])
            ],
            "validators": list(protocol.validators) + [
                Validator(validator_id="quality", validator_type="quality",
                          evaluation_phase="post_execute", severity_cap="warn",
                          criteria=["tests"])
            ],
        })
        builder = GraphBuilder(protocol, validator_fn=validator_fn)
        result = builder._post_validate_node(_make_state())

        assert result["needs_recovery"] is True
        assert len(result["spawned_subtasks"]) == 1


class NoOpCoder:
    """A coder that finds the work already present and writes nothing.

    Its ``implement`` call is the coder dispatch this suite counts: if an
    abstention wrongly routed to recovery, the spawned fix task would call it
    again.
    """

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
    """A task worktree whose branch already carries the work (an earlier
    attempt committed it and the run then failed afterwards)."""
    project = tmp_path / "project"
    project.mkdir()
    _init_repo(project)
    wt = Path(create_worktree(str(project), "task_ab", "Implement feature X"))
    (wt / "feature.py").write_text("def feature():\n    return 42\n")
    (wt / "tests").mkdir()
    (wt / "tests" / "test_feature.py").write_text(
        "from feature import feature\n\n\ndef test_feature():\n    assert feature() == 42\n"
    )
    _git("git", "add", "-A", cwd=wt)
    _git("git", "commit", "-qm", "coder: apply changes", cwd=wt)
    return str(project), str(wt)


class TestNoCoderDispatchOnCommittedWork:
    """End-to-end: an abstention on committed work never re-runs the coder."""

    def test_post_execute_abstention_rejudges_without_dispatching_a_coder(self, tmp_path):
        protocol = Protocol(
            protocol_id="abstention_e2e",
            name="Abstention E2E",
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
                # Abstain on the first post-execute pass, decide on the retry.
                if len(post_calls) == 1:
                    return [_abstention(v.validator_id) for v in validators]
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

        # The task resolves, and exactly one coder dispatch ever happened: the
        # abstention re-ran the judge rather than spawning a fix task.
        assert tree.outcome == "resolved"
        assert final_state["is_complete"] is True
        assert coder.implement_calls == 1

        # The judge was re-run in place on the unchanged work.
        assert len(post_calls) == 2
        assert final_state["metadata"]["post_validation"]["outcome"] == "passed"

        # No recovery subtask was ever created.
        assert final_state["spawned_subtasks"] == []
        ops = [call.args[0] for call in audit.append_event.call_args_list]
        assert "subtask_spawned" not in ops
        assert "abstention_rejudged" in ops

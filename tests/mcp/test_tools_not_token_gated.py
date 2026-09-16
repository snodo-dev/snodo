"""An MCP tool is governed by the protocol, not by a token the caller holds.

FILE: tests/mcp/test_tools_not_token_gated.py

Tickets hold three claims, tested here (ADR 047):

1. Every tool a mode grants is callable with no token held — the surface
   gate (`requires_token` + `_enforce_wf1`) is gone, and access is decided
   by the protocol and the mode's capability grant (a tool the mode does
   not grant stays refused as unknown).
2. The engine's token discipline is untouched: the loop still refuses to
   proceed past a `blocker`, and an `escalate` still requires a human
   decision (a signed DecisionRecord from `snodo authorize`) to clear.
3. The server's instructions say what is true — they no longer describe a
   caller token gate.
"""

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
from snodo.compiler.models import DisagreementPolicy, Mode, Protocol, Validator
from snodo.core.interfaces import ValidatorResult
from snodo.engine.loop import GraphBuilder
from snodo.engine.policy import PolicyAction, PolicyEvaluator
from snodo.mcp.server import MCPError, ProtocolMCPServer

ALL_CAPABILITIES = [
    "edit", "decide", "dispatch", "test", "validate",
    "review", "approve", "commit", "merge", "pr", "plan",
]

ALL_TOOLS_PROTOCOL = {
    "protocol_id": "ungated",
    "name": "Ungated",
    "version": "1.0.0",
    "modes": [
        {
            "mode_id": "full",
            "name": "Full",
            "tools": ALL_CAPABILITIES,
            "validators": ["security"],
        },
    ],
    "validators": [
        {"validator_id": "security", "validator_type": "security",
         "criteria": ["Check security"]},
    ],
    "disagreement_policy": "unanimous",
    "initial_mode": "full",
}

EDIT_ONLY_PROTOCOL = {
    **ALL_TOOLS_PROTOCOL,
    "protocol_id": "fenced",
    "modes": [
        {"mode_id": "editor", "name": "Editor", "tools": ["edit"],
         "validators": ["security"]},
    ],
}


@pytest.fixture
def project_dir():
    d = tempfile.mkdtemp()
    subprocess.run(["git", "init", "-q"], cwd=d, check=False)
    subprocess.run(["git", "config", "user.email", "t@t.co"], cwd=d, check=False)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=d, check=False)
    (Path(d) / "README.md").write_text("test")
    subprocess.run(["git", "add", "."], cwd=d, check=False)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=d, check=False)
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _stub_dispatch(server):
    """Replace every concrete destination with a recorder.

    The point is the GATE, not the backing MCP: with every destination
    recorded, a call that reaches the recorder proves the surface let it
    through; a call that raises proves it did not.
    """
    reached = {}

    def record(name):
        def _handler(*args, **kwargs):
            reached[name] = True
            return {"reached": name}
        return _handler

    for name, schema in server._tools.items():
        if name in server._dispatch:
            server._dispatch[name] = record(name)
        elif schema.get("mcp"):
            backing = server._mcp_map[schema["mcp"]]
            setattr(backing, schema["method"], record(name))
        else:  # pragma: no cover - registry invariant
            raise AssertionError(f"{name}: neither handler nor backing MCP")
    return reached


class TestGrantedToolsAreCallableWithoutToken:
    def _serve(self, protocol_data, project_dir, mode_id):
        protocol = Protocol(**protocol_data)
        return ProtocolMCPServer(protocol, project_dir, mode_id=mode_id)

    def test_surface_gate_is_gone_from_the_server(self, project_dir):
        server = self._serve(ALL_TOOLS_PROTOCOL, project_dir, "full")
        assert not hasattr(server, "_enforce_wf1")
        assert server._validation_token is None

    def test_every_granted_tool_is_callable_with_no_token_held(self, project_dir):
        """The whole granted surface — including every tool that once carried
        requires_token=True — is reached while holding nothing."""
        server = self._serve(ALL_TOOLS_PROTOCOL, project_dir, "full")
        reached = _stub_dispatch(server)
        granted = sorted(server._tools)
        assert granted, "mode should grant a broad surface"
        assert server._validation_token is None

        for name in granted:
            result = server.call_tool(name, {})
            assert result == {"reached": name}, (
                f"{name} was not reached with no token held"
            )
        # The calls that used to be gated at the surface are among those made
        for formerly_gated in ("commit", "stage_files", "merge_branch",
                               "delete_branch", "create_pr", "merge_pr",
                               "dispatch_task", "retry_job",
                               "decompose", "generate_spec", "propose_plan"):
            assert formerly_gated in granted
        assert reached  # nothing stubbed silently unused

    def test_a_tool_the_mode_does_not_grant_stays_refused(self, project_dir):
        """Access at the surface is the mode's grant: refusing what is not
        granted is how the protocol governs, not a token."""
        server = self._serve(EDIT_ONLY_PROTOCOL, project_dir, "editor")
        _stub_dispatch(server)
        assert server.call_tool("read_file", {}) == {"reached": "read_file"}
        with pytest.raises(MCPError, match="Unknown tool"):
            server.call_tool("commit", {"message": "x"})
        with pytest.raises(MCPError, match="Unknown tool"):
            server.call_tool("dispatch_task", {"task_spec": "x"})


class TestEngineLoopStillEnforcesTheQuorum:
    """The guarantee this ticket must not weaken, tested where it lives."""

    PROTOCOL = Protocol(
        protocol_id="loop",
        name="Loop",
        version="1.0.0",
        modes=[Mode(mode_id="producer", name="Producer",
                    tools=["edit", "test"], validators=["security"])],
        validators=[Validator(validator_id="security",
                              validator_type="security",
                              criteria=["Check"])],
        disagreement_policy=DisagreementPolicy.UNANIMOUS,
        initial_mode="producer",
    )

    def _initial_state(self, task_id="t1"):
        return {
            "task": {"id": task_id, "spec": "do the thing"},
            "current_mode": "producer",
            "iteration": 1,
            "stage": "validate",
            "validation_results": [],
            "validation_token": None,
            "artifacts": [],
            "constraints_passed": True,
            "constraint_violations": [],
            "policy_decision": None,
            "is_complete": False,
            "is_blocked": False,
            "metadata": {},
        }

    def test_a_blocker_stops_the_loop_before_execute(self):
        def blocker_validator(task, validators, shell_mcp, current_mode="", **kwargs):
            return [ValidatorResult(validator_id="security", severity="blocker",
                                    justification="no")]

        builder = GraphBuilder(self.PROTOCOL, validator_fn=blocker_validator)
        state = builder._validate_node(self._initial_state())

        assert state["is_blocked"] is True
        assert state["halt_type"] == "blocked"
        assert state["validation_token"] is None
        # and the loop routes it to the halt, never to the coder
        assert builder._route_after_validation(state) == "blocked"

    def test_no_token_never_routes_to_execute(self):
        """Even unblocked, execute is reachable only on a token the quorum
        issued — the gate moved nowhere, it was always the loop's."""
        builder = GraphBuilder(self.PROTOCOL)
        state = self._initial_state()
        state["is_blocked"] = False
        state["validation_token"] = None
        assert builder._route_after_validation(state) != "execute"

    def test_an_escalate_requires_a_human_decision(self):
        """Warn under unanimous: the policy escalates — it neither proceeds
        nor can the agent mint its own clearance."""
        warn = [ValidatorResult(validator_id="security", severity="warn",
                                justification="concern")]

        decision = PolicyEvaluator().evaluate(
            warn, DisagreementPolicy.UNANIMOUS, "pre_execute", task_ref="t1",
        )
        assert decision.action == PolicyAction.ESCALATE

        # A signed human decision (what `snodo authorize` produces) is what
        # clears the escalation — and only that.
        from cryptography.hazmat.backends import default_backend
        from cryptography.hazmat.primitives.asymmetric import rsa
        from snodo.infrastructure.decisions import (
            SigningDecisionRecordIssuer,
            VerifyOnlyDecisionRecordIssuer,
        )

        priv = rsa.generate_private_key(65537, 2048, backend=default_backend())
        signing = SigningDecisionRecordIssuer(priv)
        verify = VerifyOnlyDecisionRecordIssuer(priv.public_key())
        record = signing.issue_record(
            task_ref="t1",
            validator_id="security",
            validator_result=warn[0],
            decision="proceed",
            justification="human approves",
        )
        cleared = PolicyEvaluator(decision_issuer=verify).evaluate(
            warn, DisagreementPolicy.UNANIMOUS, "pre_execute",
            decision_records=[record.jwt], task_ref="t1",
        )
        assert cleared.action in (PolicyAction.PROCEED, PolicyAction.PROCEED_WITH_LOG)

    def test_a_blocker_is_never_overridable_by_a_human_decision(self):
        """authorize clears an escalation; there is nothing to authorize
        against a blocker — the mint itself refuses, and the policy halts.
        Exactly as before this ticket."""
        from cryptography.hazmat.backends import default_backend
        from cryptography.hazmat.primitives.asymmetric import rsa
        from snodo.infrastructure.decisions import (
            DecisionInvalidSeverityError,
            SigningDecisionRecordIssuer,
        )

        blocked = [ValidatorResult(validator_id="security", severity="blocker",
                                   justification="critical")]
        priv = rsa.generate_private_key(65537, 2048, backend=default_backend())
        signing = SigningDecisionRecordIssuer(priv)
        with pytest.raises(DecisionInvalidSeverityError):
            signing.issue_record(
                task_ref="t1", validator_id="security",
                validator_result=blocked[0], decision="proceed",
                justification="human tried",
            )
        decision = PolicyEvaluator().evaluate(
            blocked, DisagreementPolicy.UNANIMOUS, "pre_execute", task_ref="t1",
        )
        assert decision.action == PolicyAction.HALT


class TestInstructionsSayWhatIsTrue:
    def _instructions(self, project_dir):
        from snodo.mcp.transport import _build_instructions
        server = ProtocolMCPServer(
            Protocol(**ALL_TOOLS_PROTOCOL), project_dir, mode_id="full",
        )
        return _build_instructions(server)

    def test_instructions_describe_no_gate_that_is_not_there(self, project_dir):
        text = self._instructions(project_dir)
        # The old claims, verbatim in part — none may survive:
        assert "Mutations are token-gated" not in text
        assert "require a single-use" not in text
        assert "requires a token from a `pass` validate_task" not in text
        assert "authorizes the next mutating tool call" not in text
        assert "you must re-validate for each mutation cycle" not in text

    def test_instructions_state_the_new_governance(self, project_dir):
        text = self._instructions(project_dir)
        assert "ADR 047" in text
        assert "no tool call is refused for want" in text
        assert "mode grant" in text  # access is the protocol's and the mode's
        # and where the enforceable discipline actually lives:
        assert "engine loop" in text
        assert "snodo authorize" in text

    def test_async_contract_and_outcomes_are_undisturbed(self, project_dir):
        """The rewrite touched governance prose only — the async contract
        and the four outcomes still read as they did."""
        text = self._instructions(project_dir)
        assert "ASYNCHRONOUS" in text
        for outcome in ("pass", "escalate", "blocker", "validator_error"):
            assert outcome in text

"""The record of an abstention must say "no verdict" everywhere (Fixes #252).

An abstention is a validator that exhausted its tool-turn budget without
reaching a verdict (severity is None). These tests establish that:

1. a human adjudicating an abstention-caused halt/escalation can see that a
   judge abstained, WHICH judge, WHY it ran out, and WHAT was and was not
   examined;
2. an abstention survives the graph-state checkpoint and is recoverable from
   it (never coerced back to "pass");
3. a halt caused by abstentions does not name blockers that do not exist;
4. the retry evidence handed to a coder includes the fact that a judge could
   not decide;
5. no audit event records an abstention as a pass.

Plus the signed-adjudication path: a human's DecisionRecord(proceed) for an
abstaining judge retires that judge from the quorum (it is never converted
into a pass vote), and the tool loop records the examination it made.
"""

from unittest.mock import MagicMock

from snodo.compiler.models import DisagreementPolicy, Mode, Protocol, Validator
from snodo.core.interfaces import Task, ValidatorResult, result_record
from snodo.engine.loop import GraphBuilder
from snodo.engine.policy import PolicyAction, PolicyEvaluator
from snodo.engine.state import LoopState

TASK_ID = "task_001"


def _abstaining_result(validator_id="budget_judge"):
    return ValidatorResult(
        validator_id=validator_id,
        severity=None,
        justification=(
            "Validator could not reach a verdict within the allocated 20 turns."
        ),
        abstention_reason="exhausted budget after 20 turns",
        examined=[
            "prompt: preloaded diff base..HEAD",
            "turn 1: read_file src/auth.py",
            "turn 2: git_log",
        ],
        unexamined_tools=["read_file_lines", "git_show"],
    )


def _passing_result(validator_id="security"):
    return ValidatorResult(validator_id=validator_id, severity="pass",
                           justification="looks fine")


def _protocol(abstention_policy="blocking",
              validators=("security", "budget_judge")):
    kwargs = dict(
        protocol_id="p_abst", name="Abstention Record Protocol", version="1.0.0",
        modes=[Mode(mode_id="producer", name="Producer", tools=["edit"],
                    validators=list(validators))],
        validators=[
            Validator(validator_id=vid, validator_type="security",
                      criteria=[f"criterion for {vid}"])
            for vid in validators
        ],
        disagreement_policy=DisagreementPolicy.UNANIMOUS,
        initial_mode="producer",
    )
    if abstention_policy != "blocking":
        kwargs["abstention_policy"] = abstention_policy
    return Protocol(**kwargs)


def _initial_state():
    return {
        "task": {"id": TASK_ID, "spec": "Implement feature X"},
        "current_mode": "producer",
        "iteration": 0,
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


def _builder(protocol, results, audit=None):
    def validator_fn(task, validators, shell_mcp, **kwargs):
        return list(results)

    return GraphBuilder(
        protocol,
        validator_fn=validator_fn,
        audit_log=audit if audit is not None else MagicMock(),
    )


def _builder_with_session(protocol, results):
    builder = _builder(protocol, results)
    session = MagicMock()
    session.checkpoint.decisions = {}
    mgr = MagicMock()
    mgr.load_session.return_value = session
    builder._session_manager = mgr
    builder._session_id = "sess-1"
    return builder, mgr, session


class _RecordingAudit:
    def __init__(self):
        self.events = []

    def append_event(self, event_type, data):
        self.events.append((event_type, data))


class TestAdjudicationPathCarriesTheAbstention:
    """Requirement: a human can see that a judge abstained and which one."""

    def test_blocking_halt_writes_adjudicable_entry_with_full_detail(self):
        """pass + abstain under the default blocking policy halts; the pending
        decision must name the silent judge and tell the human why, what it
        examined, and what it never got to."""
        builder, mgr, session = _builder_with_session(
            _protocol(), [_passing_result(), _abstaining_result()],
        )
        result = builder._validate_node(_initial_state())

        assert result["is_blocked"] is True
        calls = [c for c in mgr.update_decision.call_args_list
                 if c[0][1] == "pending_decisions"]
        assert calls, "no pending_decisions write for the abstaining judge"
        entry = calls[-1][0][2][TASK_ID]
        assert entry["type"] == "adjudicate"
        assert entry["validator_id"] == "budget_judge"
        assert entry["severity"] is None
        assert "exhausted budget" in entry["abstention_reason"]
        assert any("read_file" in e for e in entry["examined"])
        assert "git_show" in entry["unexamined_tools"]

    def test_non_blocking_escalation_payload_shows_abstainer_as_no_verdict(self):
        """Under non_blocking the same quorum escalates; the pending
        disagreement and its audit event must show severity None plus the
        abstention story — never 'pass'."""
        audit = _RecordingAudit()
        builder = _builder(
            _protocol(abstention_policy="non_blocking"),
            [_passing_result(), _abstaining_result()],
            audit=audit,
        )
        result = builder._validate_node(_initial_state())

        pd = result["pending_disagreement"]
        assert pd is not None
        abstainer = next(r for r in pd["validator_results"]
                         if r["validator_id"] == "budget_judge")
        assert abstainer["severity"] is None
        assert "exhausted budget" in abstainer["abstention_reason"]
        assert any("git_log" in e for e in abstainer["examined"])
        assert pd["policy_decision"]["abstain_count"] == 1

        escalated = [d for e, d in audit.events
                     if e == "disagreement_escalated"]
        assert escalated
        entry = next(r for r in escalated[-1]["validator_results"]
                     if r["validator_id"] == "budget_judge")
        assert entry["severity"] is None
        assert entry["abstention_reason"]

    def test_abstaining_judge_is_not_spec_critique(self):
        """Silence is not a critique: an abstaining judges_spec validator
        must not route the task into spec authoring on the strength of
        prose that never judged anything."""
        proto = Protocol(
            protocol_id="p_sc", name="x", version="1.0.0",
            modes=[Mode(mode_id="producer", name="P", tools=["edit"],
                        validators=["budget_judge"])],
            validators=[Validator(validator_id="budget_judge",
                                  validator_type="security",
                                  criteria=["c"], judges_spec=True)],
            disagreement_policy=DisagreementPolicy.UNANIMOUS,
            abstention_policy="non_blocking",
            initial_mode="producer",
        )
        builder = _builder(proto, [_abstaining_result()])
        result = builder._validate_node(_initial_state())
        assert result.get("needs_spec_authoring") is not True


class TestSignedAdjudicationRetiresAnAbstention:
    """A human-signed record makes an abstention adjudicable — as an
    abstention, never as a pass vote."""

    def _keypair_issuers(self):
        from cryptography.hazmat.primitives.asymmetric import rsa
        from snodo.infrastructure.decisions import (
            SigningDecisionRecordIssuer, VerifyOnlyDecisionRecordIssuer,
        )
        priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        return (SigningDecisionRecordIssuer(priv),
                VerifyOnlyDecisionRecordIssuer(priv.public_key()))

    def test_record_mints_as_abstain_and_policy_honours_it(self):
        signer, verifier = self._keypair_issuers()
        abstainer = _abstaining_result()
        record = signer.issue_record(
            task_ref=TASK_ID,
            validator_id=abstainer.validator_id,
            validator_result=abstainer,
            decision="proceed",
            justification="proceed without the missing verdict",
        )
        assert record.adjudicated_severity == "abstain"

        evaluator = PolicyEvaluator(decision_issuer=verifier,
                                    abstention_policy="blocking")
        decision = evaluator.evaluate(
            [_passing_result(), abstainer],
            DisagreementPolicy.UNANIMOUS, "pre_execute",
            decision_records=[record.jwt], task_ref=TASK_ID,
        )
        # The abstainer is retired from the quorum — not counted as a pass.
        assert decision.action == PolicyAction.PROCEED
        assert decision.pass_count == 1
        assert decision.total_count == 1
        assert decision.abstain_count == 1
        assert "abstention(s) retired by human DecisionRecord" in decision.justification

    def test_unadjudicated_abstention_still_halts_blocking(self):
        """With no signed record the decision behaviour is unchanged."""
        evaluator = PolicyEvaluator()
        decision = evaluator.evaluate(
            [_passing_result(), _abstaining_result()],
            DisagreementPolicy.UNANIMOUS, "pre_execute",
        )
        assert decision.action == PolicyAction.HALT
        assert decision.abstain_count == 1
        assert decision.pass_count == 1
        assert decision.total_count == 2


class TestAbstentionSurvivesCheckpoint:
    """The flag must not be lost at the engine's own state boundary."""

    def test_round_trip_restores_the_full_abstention(self):
        builder = _builder(_protocol(), [])
        state = LoopState(task=Task(id=TASK_ID, spec="s"), current_mode="producer")
        state.validation_results = [_passing_result(), _abstaining_result()]

        d = builder._state_to_dict(state)
        stored = [r for r in d["validation_results"]
                  if r["validator_id"] == "budget_judge"][0]
        assert stored["severity"] is None
        assert "exhausted budget" in stored["abstention_reason"]
        assert any("git_log" in e for e in stored["examined"])
        assert "git_show" in stored["unexamined_tools"]

        restored = builder._dict_to_state(d)
        r = next(r for r in restored.validation_results
                 if r.validator_id == "budget_judge")
        assert r.severity is None
        assert r.abstained()
        assert r.abstention_reason == _abstaining_result().abstention_reason
        assert r.examined == _abstaining_result().examined
        assert r.unexamined_tools == _abstaining_result().unexamined_tools

    def test_missing_severity_never_defaults_to_pass(self):
        """A stored result without a severity key means no verdict; restoring
        it as 'pass' was the false record this fixes."""
        builder = _builder(_protocol(), [])
        d = _initial_state()
        d["validation_results"] = [{"validator_id": "ghost", "justification": ""}]
        restored = builder._dict_to_state(d)
        assert restored.validation_results[0].severity is None


class TestAbstentionHaltNamesNoBlockers:
    """A halt caused by abstentions must say so, not claim blockers."""

    def test_halt_audit_reason_and_lists(self):
        audit = _RecordingAudit()
        builder = _builder(_protocol(), [], audit=audit)
        state = LoopState(task=Task(id=TASK_ID, spec="s"), current_mode="producer")
        state.validation_results = [_passing_result(), _abstaining_result()]
        state.is_blocked = True
        state.halt_type = "blocked"

        builder._blocked_node(builder._state_to_dict(state))

        halts = [d for e, d in audit.events if e == "halt"]
        assert halts
        halt = halts[-1]
        assert halt["blocker_validators"] == []
        assert halt["abstained_validators"] == ["budget_judge"]
        assert "abstained" in halt["reason"]
        assert "blocker" not in halt["reason"].lower()


class TestRetryEvidenceIncludesSilence:
    """The next attempt's coder must learn that a judge could not decide."""

    def test_failure_context_carries_the_abstention(self):
        builder, mgr, session = _builder_with_session(_protocol(), [])
        state = LoopState(task=Task(id=TASK_ID, spec="s"), current_mode="producer")
        state.halt_type = "blocked"
        builder._auto_write_failure_context(
            state, [_passing_result(), _abstaining_result()],
        )
        failures = mgr.update_decision.call_args[0][2]
        fvs = failures[TASK_ID]["failed_validators"]
        entry = next(v for v in fvs if v["validator_id"] == "budget_judge")
        assert entry["severity"] is None
        assert "could not decide" in entry["justification"]
        assert "exhausted budget" in entry["justification"]


class TestNoAuditEventRecordsAbstentionAsPass:
    """Sweep every event a validating run emits."""

    def test_full_validate_node_event_sweep(self):
        audit = _RecordingAudit()
        builder = _builder(
            _protocol(abstention_policy="non_blocking"),
            [_passing_result(), _abstaining_result()],
            audit=audit,
        )
        builder._validate_node(_initial_state())
        assert audit.events, "expected audit events to be emitted"

        def walk(node):
            if isinstance(node, dict):
                if node.get("validator_id") == "budget_judge" and "severity" in node:
                    yield node
                for v in node.values():
                    yield from walk(v)
            elif isinstance(node, list):
                for item in node:
                    yield from walk(item)

        seen = 0
        for event_type, data in audit.events:
            for entry in walk(data):
                seen += 1
                assert entry["severity"] is None, (
                    f"{event_type} records the abstainer as "
                    f"{entry['severity']!r}"
                )
        assert seen, "abstainer appeared in no audit event at all"

    def test_result_record_shape(self):
        """The shared serializer is honest for both verdicts and abstentions."""
        assert result_record(_passing_result())["severity"] == "pass"
        rec = result_record(_abstaining_result())
        assert rec["severity"] is None
        assert rec["abstention_reason"]
        assert rec["examined"] and rec["unexamined_tools"]
        # A genuine pass must NOT carry abstention keys.
        assert "abstention_reason" not in result_record(_passing_result())

    def test_record_survives_pydantic_validation(self):
        """ValidatorResult with severity None is the abstention record."""
        rec = _abstaining_result().record()
        assert rec["severity"] is None
        assert _abstaining_result().abstained() is True
        assert _passing_result().abstained() is False

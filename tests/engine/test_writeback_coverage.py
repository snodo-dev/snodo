"""Branch-coverage tests for snodo/engine/nodes/writeback.py.

Targets the un-hit lines: 22-48, 57-89, 101-102, 106-112, 118-131,
169-170, 173, 199-200, 203, 247.
No source changes — stubs only.
"""

import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from snodo.compiler.models import Protocol, Mode, Validator
from snodo.core.interfaces import Task, ValidatorResult
from snodo.engine.loop import GraphBuilder
from snodo.engine.state import LoopState


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_protocol():
    return Protocol(
        protocol_id="test", name="Test",
        modes=[Mode(mode_id="producer", name="Producer", tools=[], validators=[])],
        validators=[Validator(validator_id="v1", validator_type="security",
                              evaluation_phase="pre_execute")],
        initial_mode="producer",
    )


def _make_task(tid="t1", spec="do something"):
    return Task(id=tid, spec=spec)


def _make_loop_state(task=None, artifacts=None):
    t = task or _make_task()
    state = LoopState(task=t, current_mode="producer")
    if artifacts:
        state.artifacts = artifacts
    return state


def _make_session_mock(decisions=None):
    """Return a (session_manager, session) pair with a mutable decisions dict."""
    session = MagicMock()
    session.checkpoint.decisions = decisions if decisions is not None else {}
    mgr = MagicMock()
    mgr.load_session.return_value = session
    return mgr, session


def _make_builder_with_session(decisions=None):
    protocol = _make_protocol()
    builder = GraphBuilder(protocol)
    mgr, session = _make_session_mock(decisions)
    builder._session_manager = mgr
    builder._session_id = "sess-1"
    return builder, mgr, session


# ---------------------------------------------------------------------------
# _auto_write_pending_decisions
# ---------------------------------------------------------------------------

class TestAutoWritePendingDecisions:
    def test_no_session_manager_returns_early(self):
        protocol = _make_protocol()
        builder = GraphBuilder(protocol)
        builder._session_manager = None
        state = _make_loop_state()
        # Should not raise
        builder._auto_write_pending_decisions(state, [])

    def test_load_session_exception_returns_early(self):
        protocol = _make_protocol()
        builder = GraphBuilder(protocol)
        mgr = MagicMock()
        mgr.load_session.side_effect = RuntimeError("db gone")
        builder._session_manager = mgr
        builder._session_id = "sess-1"
        state = _make_loop_state()
        builder._auto_write_pending_decisions(state, [])  # no raise

    def test_writes_pending_decision_for_blocker(self):
        builder, mgr, session = _make_builder_with_session()
        session.checkpoint.decisions = {"pending_decisions": {}}
        state = _make_loop_state()
        results = [ValidatorResult(validator_id="sec", severity="blocker", justification="bad")]
        builder._auto_write_pending_decisions(state, results)
        mgr.update_decision.assert_called_once()
        call_args = mgr.update_decision.call_args
        pending = call_args[0][2]
        assert "t1" in pending
        assert pending["t1"]["validator_id"] == "sec"
        assert pending["t1"]["severity"] == "blocker"

    def test_writes_pending_decision_for_warn(self):
        builder, mgr, session = _make_builder_with_session()
        session.checkpoint.decisions = {}
        state = _make_loop_state()
        results = [ValidatorResult(validator_id="linter", severity="warn", justification="style")]
        builder._auto_write_pending_decisions(state, results)
        pending = mgr.update_decision.call_args[0][2]
        assert "t1" in pending
        assert pending["t1"]["severity"] == "warn"

    def test_skips_pass_severity(self):
        builder, mgr, session = _make_builder_with_session()
        session.checkpoint.decisions = {}
        state = _make_loop_state()
        results = [ValidatorResult(validator_id="v1", severity="pass", justification="ok")]
        builder._auto_write_pending_decisions(state, results)
        mgr.update_decision.assert_called_once()
        pending = mgr.update_decision.call_args[0][2]
        assert "t1" not in pending

    def test_pending_not_dict_reset(self):
        """If existing pending_decisions isn't a dict, it's reset."""
        builder, mgr, session = _make_builder_with_session()
        session.checkpoint.decisions = {"pending_decisions": "invalid"}
        state = _make_loop_state()
        results = [ValidatorResult(validator_id="v1", severity="error", justification="crash")]
        builder._auto_write_pending_decisions(state, results)
        pending = mgr.update_decision.call_args[0][2]
        assert isinstance(pending, dict)


# ---------------------------------------------------------------------------
# _auto_write_failure_context
# ---------------------------------------------------------------------------

class TestAutoWriteFailureContext:
    def test_no_session_manager_returns_early(self):
        protocol = _make_protocol()
        builder = GraphBuilder(protocol)
        builder._session_manager = None
        builder._auto_write_failure_context(_make_loop_state(), [])

    def test_load_session_exception_returns_early(self):
        protocol = _make_protocol()
        builder = GraphBuilder(protocol)
        mgr = MagicMock()
        mgr.load_session.side_effect = IOError("no session")
        builder._session_manager = mgr
        builder._session_id = "sess-1"
        builder._auto_write_failure_context(_make_loop_state(), [])

    def test_writes_new_failure_entry(self):
        builder, mgr, session = _make_builder_with_session()
        session.checkpoint.decisions = {}
        state = _make_loop_state(artifacts=["src/main.py"])
        results = [ValidatorResult(validator_id="sec", severity="blocker", justification="vuln")]
        builder._auto_write_failure_context(state, results)
        failures = mgr.update_decision.call_args[0][2]
        entry = failures["t1"]
        assert entry["attempt"] == 1
        assert entry["spec"] == "do something"
        assert len(entry["failed_validators"]) == 1
        assert entry["files_changed"] == ["src/main.py"]

    def test_increments_attempt_on_retry(self):
        builder, mgr, session = _make_builder_with_session()
        session.checkpoint.decisions = {
            "task_failure": {"t1": {"attempt": 3, "spec": "do something"}}
        }
        state = _make_loop_state()
        results = [ValidatorResult(validator_id="v", severity="warn", justification="w")]
        builder._auto_write_failure_context(state, results)
        failures = mgr.update_decision.call_args[0][2]
        assert failures["t1"]["attempt"] == 4

    def test_failures_not_dict_reset(self):
        builder, mgr, session = _make_builder_with_session()
        session.checkpoint.decisions = {"task_failure": "corrupt"}
        state = _make_loop_state()
        builder._auto_write_failure_context(state, [])
        failures = mgr.update_decision.call_args[0][2]
        assert isinstance(failures, dict)

    def test_pass_severity_not_included(self):
        builder, mgr, session = _make_builder_with_session()
        session.checkpoint.decisions = {}
        state = _make_loop_state()
        results = [
            ValidatorResult(validator_id="v1", severity="pass", justification="ok"),
            ValidatorResult(validator_id="v2", severity="blocker", justification="bad"),
        ]
        builder._auto_write_failure_context(state, results)
        failures = mgr.update_decision.call_args[0][2]
        fvs = failures["t1"]["failed_validators"]
        assert len(fvs) == 1
        assert fvs[0]["validator_id"] == "v2"


# ---------------------------------------------------------------------------
# _clear_failure_context
# ---------------------------------------------------------------------------

class TestClearFailureContext:
    def test_no_session_manager_returns_early(self):
        protocol = _make_protocol()
        builder = GraphBuilder(protocol)
        builder._session_manager = None
        builder._clear_failure_context(_make_loop_state())

    def test_load_session_exception_returns_early(self):
        """Lines 101-102: except Exception: return in load_session."""
        protocol = _make_protocol()
        builder = GraphBuilder(protocol)
        mgr = MagicMock()
        mgr.load_session.side_effect = RuntimeError("boom")
        builder._session_manager = mgr
        builder._session_id = "sess-1"
        builder._clear_failure_context(_make_loop_state())  # no raise

    def test_task_not_in_failures_no_update(self):
        builder, mgr, session = _make_builder_with_session()
        session.checkpoint.decisions = {"task_failure": {"other": {}}}
        builder._clear_failure_context(_make_loop_state())
        mgr.update_decision.assert_not_called()

    def test_clears_existing_failure(self):
        """Lines 106-112: task in failures → delete + update_decision called."""
        builder, mgr, session = _make_builder_with_session()
        session.checkpoint.decisions = {
            "task_failure": {"t1": {"attempt": 2}, "other": {}}
        }
        builder._clear_failure_context(_make_loop_state())
        mgr.update_decision.assert_called_once()
        failures = mgr.update_decision.call_args[0][2]
        assert "t1" not in failures
        assert "other" in failures

    def test_update_decision_exception_swallowed(self):
        """Lines 111-112: update_decision raises → silently swallowed (pass)."""
        builder, mgr, session = _make_builder_with_session()
        session.checkpoint.decisions = {"task_failure": {"t1": {"attempt": 1}}}
        mgr.update_decision.side_effect = RuntimeError("write error")
        # Should not raise
        builder._clear_failure_context(_make_loop_state())


# ---------------------------------------------------------------------------
# _merge_into_job_state
# ---------------------------------------------------------------------------

class TestMergeIntoJobState:
    def test_no_job_id_returns_early(self):
        protocol = _make_protocol()
        builder = GraphBuilder(protocol)
        builder._job_id = ""
        builder._merge_into_job_state({"key": "val"})  # no raise

    def test_no_project_root_returns_early(self):
        protocol = _make_protocol()
        builder = GraphBuilder(protocol)
        builder._job_id = "job1"
        builder._project_root = ""
        builder._merge_into_job_state({"key": "val"})  # no raise

    def test_nonexistent_job_dir_returns_early(self, tmp_path):
        protocol = _make_protocol()
        builder = GraphBuilder(protocol)
        builder._job_id = "nonexistent"
        builder._project_root = str(tmp_path)
        builder._merge_into_job_state({"key": "val"})  # no raise

    def test_writes_new_state_json(self, tmp_path):
        """Lines 118-131: job_dir exists → writes state.json from scratch."""
        job_dir = tmp_path / ".snodo" / "jobs" / "job1"
        job_dir.mkdir(parents=True)
        protocol = _make_protocol()
        builder = GraphBuilder(protocol)
        builder._job_id = "job1"
        builder._project_root = str(tmp_path)
        builder._merge_into_job_state({"status": "done", "count": 5})
        state_path = job_dir / "state.json"
        assert state_path.exists()
        data = json.loads(state_path.read_text())
        assert data["status"] == "done"
        assert data["count"] == 5

    def test_merges_into_existing_state_json(self, tmp_path):
        """Existing state.json is merged (existing keys preserved)."""
        job_dir = tmp_path / ".snodo" / "jobs" / "job2"
        job_dir.mkdir(parents=True)
        state_path = job_dir / "state.json"
        state_path.write_text(json.dumps({"existing": "yes"}))
        protocol = _make_protocol()
        builder = GraphBuilder(protocol)
        builder._job_id = "job2"
        builder._project_root = str(tmp_path)
        builder._merge_into_job_state({"new_key": "new_val"})
        data = json.loads(state_path.read_text())
        assert data["existing"] == "yes"
        assert data["new_key"] == "new_val"

    def test_corrupted_state_json_reset(self, tmp_path):
        """Corrupt state.json → treated as empty dict, write succeeds."""
        job_dir = tmp_path / ".snodo" / "jobs" / "job3"
        job_dir.mkdir(parents=True)
        (job_dir / "state.json").write_text("{{invalid json")
        protocol = _make_protocol()
        builder = GraphBuilder(protocol)
        builder._job_id = "job3"
        builder._project_root = str(tmp_path)
        builder._merge_into_job_state({"fresh": True})
        data = json.loads((job_dir / "state.json").read_text())
        assert data["fresh"] is True


# ---------------------------------------------------------------------------
# _auto_write_halt_payload — session exception + halt-not-dict branches
# ---------------------------------------------------------------------------

class TestAutoWriteHaltPayloadEdges:
    def _make_state_blocked(self, task=None):
        t = task or _make_task()
        state = LoopState(task=t, current_mode="producer")
        state.is_complete = False
        state.is_blocked = True
        state.halt_type = "constraint"
        state.constraint_violations = ["v1"]
        return state

    def test_load_session_exception_returns_early(self):
        """Lines 169-170: load_session raises → early return."""
        protocol = _make_protocol()
        builder = GraphBuilder(protocol)
        builder._merge_into_job_state = MagicMock()
        mgr = MagicMock()
        mgr.load_session.side_effect = RuntimeError("db dead")
        builder._session_manager = mgr
        builder._session_id = "sess-1"
        state = self._make_state_blocked()
        builder._auto_write_halt_payload(state)
        builder._merge_into_job_state.assert_called_once()  # direct write still happens
        mgr.update_decision.assert_not_called()  # session path aborted

    def test_halt_not_dict_reset_to_empty(self):
        """Line 173: existing halt value that's not a dict → reset to {}."""
        builder, mgr, session = _make_builder_with_session(
            decisions={"halt": "corrupt_string"}
        )
        builder._merge_into_job_state = MagicMock()
        state = self._make_state_blocked()
        builder._auto_write_halt_payload(state)
        call_args = mgr.update_decision.call_args[0]
        halt_dict = call_args[2]
        assert isinstance(halt_dict, dict)
        assert "t1" in halt_dict


# ---------------------------------------------------------------------------
# _auto_write_classification — session exception + classifications-not-dict
# ---------------------------------------------------------------------------

class TestAutoWriteClassificationEdges:
    def _make_classified_state(self):
        task = Task(id="t1", spec="do something", flow_type="feature", wave_id="w1")
        return LoopState(task=task, current_mode="producer")

    def test_no_session_returns_after_job_write(self):
        protocol = _make_protocol()
        builder = GraphBuilder(protocol)
        builder._merge_into_job_state = MagicMock()
        builder._session_manager = None
        state = self._make_classified_state()
        builder._auto_write_classification(state)
        builder._merge_into_job_state.assert_called_once()

    def test_load_session_exception_returns_early(self):
        """Lines 199-200: load_session raises → early return."""
        protocol = _make_protocol()
        builder = GraphBuilder(protocol)
        builder._merge_into_job_state = MagicMock()
        mgr = MagicMock()
        mgr.load_session.side_effect = IOError("session gone")
        builder._session_manager = mgr
        builder._session_id = "sess-1"
        state = self._make_classified_state()
        builder._auto_write_classification(state)
        mgr.update_decision.assert_not_called()

    def test_classifications_not_dict_reset(self):
        """Line 203: existing classifications value not a dict → reset to {}."""
        builder, mgr, session = _make_builder_with_session(
            decisions={"classification": 42}  # not a dict
        )
        builder._merge_into_job_state = MagicMock()
        state = self._make_classified_state()
        builder._auto_write_classification(state)
        classifications = mgr.update_decision.call_args[0][2]
        assert isinstance(classifications, dict)
        assert "t1" in classifications

    def test_no_flow_or_wave_skips_job_write(self):
        """No flow_type and no wave_id → _merge_into_job_state not called."""
        builder, mgr, session = _make_builder_with_session()
        session.checkpoint.decisions = {}
        builder._merge_into_job_state = MagicMock()
        task = Task(id="t1", spec="spec")
        state = LoopState(task=task, current_mode="producer")
        # flow_type=None, wave_id=None by default
        builder._auto_write_classification(state)
        builder._merge_into_job_state.assert_not_called()


# ---------------------------------------------------------------------------
# _maybe_respawn_coder — line 247: fresh_coder._job_id = self._job_id
# ---------------------------------------------------------------------------

class TestMaybeRespawnCoderJobId:
    def test_job_id_propagated_to_fresh_coder(self):
        """Line 247: when builder._job_id is set, it's copied to fresh_coder."""
        from snodo.infrastructure.decisions import SigningDecisionRecordIssuer, VerifyOnlyDecisionRecordIssuer
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.backends import default_backend
        import jwt
        from datetime import datetime, timezone

        priv = rsa.generate_private_key(65537, 2048, backend=default_backend())
        signer = SigningDecisionRecordIssuer(priv)
        verifier = VerifyOnlyDecisionRecordIssuer(priv.public_key())

        payload = {
            "iat": datetime.now(timezone.utc),
            "task_ref": "t1",
            "type": "set_model",
            "proposed_model": "gemini/gemini-2.5-pro",
            "scope": "coder",
            "justification": "test",
            "resolved_by": "human",
        }
        token = jwt.encode(payload, priv, algorithm="RS256")

        protocol = _make_protocol()
        from snodo.coders import LiteLLMAdapter
        builder = GraphBuilder(protocol, coder=LiteLLMAdapter(model="claude-sonnet-4-20250514"))
        builder._decision_issuer = verifier
        builder._authorized_decisions = [token]
        builder._job_id = "job-xyz"
        builder.workspace_mcp = None

        builder._maybe_respawn_coder()

        assert builder.coder._job_id == "job-xyz"
        assert builder._default_model == "gemini/gemini-2.5-pro"

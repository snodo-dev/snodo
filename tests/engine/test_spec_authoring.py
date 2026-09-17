"""Spec-authoring must receive only spec-quality critique (Fixes #35).

The rewriter translates a raw intent into a well-formed spec.  Only a
judges_spec validator's critique is about the wording; every other validator's
objection is about the work and must not be laundered into the spec (that would
be a redefinition, not a rewrite).
"""

from unittest.mock import MagicMock

from snodo.compiler.models import DisagreementPolicy, Mode, Protocol, Validator
from snodo.core.interfaces import Task, ValidatorResult
from snodo.engine.loop import GraphBuilder
from snodo.engine.state import LoopState


def _make_response(content: str):
    msg = MagicMock()
    msg.content = content
    resp = MagicMock()
    resp.choices = [MagicMock(message=msg)]
    return resp


def _protocol(validators):
    return Protocol(
        protocol_id="test_protocol",
        name="Test Protocol",
        version="1.0.0",
        modes=[Mode(mode_id="producer", name="Producer", tools=["edit"],
                     validators=[v.validator_id for v in validators])],
        validators=validators,
        disagreement_policy=DisagreementPolicy.UNANIMOUS,
        initial_mode="producer",
    )


def _task(spec="Implement feature X"):
    return Task(id="task_001", spec=spec)


def _validate_state(task):
    return {
        "task": {"id": task.id, "spec": task.spec},
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


# ---------------------------------------------------------------------------
# _validate_node: spec-quality critique triggers authoring; non-spec does not
# ---------------------------------------------------------------------------

class TestCritiqueReachesAuthor:
    def test_spec_quality_critique_reaches_author(self):
        """A judges_spec validator's warn routes to authoring with its critique."""
        meta_spec = Validator(
            validator_id="meta-spec", validator_type="architecture",
            criteria=["Check spec shape"], judges_spec=True,
        )
        protocol = _protocol([meta_spec])
        builder = GraphBuilder(protocol, validator_fn=lambda *a, **k: [
            ValidatorResult(validator_id="meta-spec", severity="warn",
                            justification="Spec is code-prescriptive"),
        ])
        result = builder._validate_node(_validate_state(_task()))
        assert result.get("needs_spec_authoring") is True
        critique = result["metadata"].get("spec_critique", [])
        assert [c["validator_id"] for c in critique] == ["meta-spec"]

    def test_non_spec_critique_does_not_reach_author(self):
        """A non-spec validator's warn escalates, it does not author."""
        architecture = Validator(
            validator_id="architecture", validator_type="architecture",
            criteria=["Check design"], judges_spec=False,
        )
        protocol = _protocol([architecture])
        builder = GraphBuilder(protocol, validator_fn=lambda *a, **k: [
            ValidatorResult(validator_id="architecture", severity="warn",
                            justification="Architecture is violated"),
        ])
        result = builder._validate_node(_validate_state(_task()))
        # Non-spec escalation: no authoring, normal escalate halt.
        assert result.get("needs_spec_authoring") is False
        assert result.get("is_blocked") is True
        assert result.get("halt_type") == "escalated"

    def test_non_spec_critique_excluded_from_authoring_input(self):
        """When spec and non-spec both warn, only the spec critique is passed."""
        meta_spec = Validator(
            validator_id="meta-spec", validator_type="architecture",
            criteria=["Check spec shape"], judges_spec=True,
        )
        architecture = Validator(
            validator_id="architecture", validator_type="architecture",
            criteria=["Check design"], judges_spec=False,
        )
        protocol = _protocol([meta_spec, architecture])
        builder = GraphBuilder(protocol, validator_fn=lambda *a, **k: [
            ValidatorResult(validator_id="meta-spec", severity="warn",
                            justification="Spec is code-prescriptive"),
            ValidatorResult(validator_id="architecture", severity="warn",
                            justification="The work is mis-designed"),
        ])
        result = builder._validate_node(_validate_state(_task()))
        assert result.get("needs_spec_authoring") is True
        critique = result["metadata"].get("spec_critique", [])
        assert [c["validator_id"] for c in critique] == ["meta-spec"]


# ---------------------------------------------------------------------------
# _spec_authoring_reentry: prompt content + provenance
# ---------------------------------------------------------------------------

class TestAuthoringPromptAndProvenance:
    def _builder_with_authoring(self, protocol, spec_critique):
        builder = GraphBuilder(protocol)
        authored = "A clean, well-formed spec."
        builder._classifier_completion_fn = MagicMock(
            return_value=_make_response(authored)
        )
        loop_state = LoopState(
            task=_task(),
            current_mode="producer",
            needs_spec_authoring=True,
            metadata={"spec_critique": spec_critique},
        )
        return builder, loop_state, authored

    def test_only_spec_quality_critique_in_prompt(self):
        """The authoring prompt contains only the judges_spec validator's text."""
        meta_spec = Validator(
            validator_id="meta-spec", validator_type="architecture",
            criteria=["Check spec shape"], judges_spec=True,
        )
        architecture = Validator(
            validator_id="architecture", validator_type="architecture",
            criteria=["Check design"], judges_spec=False,
        )
        protocol = _protocol([meta_spec, architecture])
        critique = [
            {"validator_id": "meta-spec", "justification": "Spec is code-prescriptive"},
            {"validator_id": "architecture", "justification": "The work is mis-designed"},
        ]
        builder, loop_state, _ = self._builder_with_authoring(protocol, critique)

        builder._spec_authoring_reentry(loop_state)

        prompt = builder._classifier_completion_fn.call_args[1]["messages"][0]["content"]
        assert "Spec is code-prescriptive" in prompt
        assert "The work is mis-designed" not in prompt

    def test_provenance_recorded(self):
        """The payload shows the spec was authored, at which attempt, from what."""
        meta_spec = Validator(
            validator_id="meta-spec", validator_type="architecture",
            criteria=["Check spec shape"], judges_spec=True,
        )
        protocol = _protocol([meta_spec])
        critique = [
            {"validator_id": "meta-spec", "justification": "Spec is code-prescriptive"},
        ]
        builder, loop_state, authored = self._builder_with_authoring(protocol, critique)

        original = loop_state.task.spec
        builder._spec_authoring_reentry(loop_state)

        prov = loop_state.metadata["spec_authoring"]
        assert prov["attempt"] == 1
        assert prov["triggered_by"] == ["meta-spec"]
        assert prov["original"] == original
        assert prov["authored"] == authored

    def test_repro_does_not_feed_its_own_objection(self):
        """Architecture's stale objection cannot re-enter the spec.

        The reproduction: architecture blocked on a stale criterion, its
        justification went into the rewrite, the author added a sentence about
        it, architecture then blocked on that sentence.  With spec-only
        critique, architecture's objection never reaches the author, so the
        loop cannot converge on its own objection.
        """
        meta_spec = Validator(
            validator_id="meta-spec", validator_type="architecture",
            criteria=["Check spec shape"], judges_spec=True,
        )
        architecture = Validator(
            validator_id="architecture", validator_type="architecture",
            criteria=["Check design"], judges_spec=False,
        )
        protocol = _protocol([meta_spec, architecture])
        builder = GraphBuilder(protocol, validator_fn=lambda *a, **k: [
            ValidatorResult(validator_id="meta-spec", severity="warn",
                            justification="Spec is code-prescriptive"),
            ValidatorResult(validator_id="architecture", severity="warn",
                            justification="wallet signing interface is missing"),
        ])

        # First pass: validate → authoring (only meta-spec critique).
        result = builder._validate_node(_validate_state(_task()))
        assert result.get("needs_spec_authoring") is True
        critique = result["metadata"].get("spec_critique", [])
        assert [c["validator_id"] for c in critique] == ["meta-spec"]
        assert all("wallet" not in c["justification"] for c in critique)

        # The rewriter prompt must not contain architecture's objection.
        builder._classifier_completion_fn = MagicMock(
            return_value=_make_response("A clean spec with no wallet mention.")
        )
        loop_state = builder._dict_to_state({
            **_validate_state(_task()),
            "metadata": {"spec_critique": critique},
            "needs_spec_authoring": True,
        })
        builder._spec_authoring_reentry(loop_state)
        prompt = builder._classifier_completion_fn.call_args[1]["messages"][0]["content"]
        assert "wallet" not in prompt
        assert "Spec is code-prescriptive" in prompt

    def test_halt_payload_carries_spec_authoring_provenance(self):
        """The halt payload exposes spec_authoring so a blocker's origin is visible."""
        meta_spec = Validator(
            validator_id="meta-spec", validator_type="architecture",
            criteria=["Check spec shape"], judges_spec=True,
        )
        protocol = _protocol([meta_spec])
        builder, loop_state, _ = self._builder_with_authoring(protocol, [
            {"validator_id": "meta-spec", "justification": "Spec is code-prescriptive"},
        ])
        builder._spec_authoring_reentry(loop_state)
        loop_state.is_blocked = True
        loop_state.halt_type = "escalated"

        payload = builder._build_halt_payload(loop_state)
        assert payload["spec_authoring"] == loop_state.metadata["spec_authoring"]
        assert payload["spec_authoring"]["attempt"] == 1


# ---------------------------------------------------------------------------
# Live surfacing: the rewrite is printed when it is produced (Fixes #36)
# ---------------------------------------------------------------------------

def _builder_with_authoring(protocol, spec_critique):
    builder = GraphBuilder(protocol)
    authored = "A clean, well-formed spec."
    builder._classifier_completion_fn = MagicMock(
        return_value=_make_response(authored)
    )
    loop_state = LoopState(
        task=_task(),
        current_mode="producer",
        needs_spec_authoring=True,
        metadata={"spec_critique": spec_critique},
    )
    return builder, loop_state, authored


class TestSpecAuthoringLiveSurface:
    """The authored spec is surfaced where it happens, not only in the payload."""

    def test_authored_spec_printed_with_critique(self, capsys):
        """When authoring fires, the original, authored spec, and critique print."""
        meta_spec = Validator(
            validator_id="meta-spec", validator_type="architecture",
            criteria=["Check spec shape"], judges_spec=True,
        )
        protocol = _protocol([meta_spec])
        critique = [
            {"validator_id": "meta-spec", "justification": "Spec is code-prescriptive"},
        ]
        builder, loop_state, authored = _builder_with_authoring(protocol, critique)
        original = loop_state.task.spec

        builder._spec_authoring_reentry(loop_state)

        out = capsys.readouterr().out
        assert "Spec authored (attempt 1)" in out
        assert "triggered by meta-spec" in out
        assert f"Original: {original}" in out
        assert f"Authored: {authored}" in out
        assert "Spec is code-prescriptive" in out

    def test_task_spec_replaced_and_provenance_recorded(self):
        """The running task.spec is replaced with the authored text, and the
        provenance block records the original."""
        meta_spec = Validator(
            validator_id="meta-spec", validator_type="architecture",
            criteria=["Check spec shape"], judges_spec=True,
        )
        protocol = _protocol([meta_spec])
        critique = [
            {"validator_id": "meta-spec", "justification": "Spec is code-prescriptive"},
        ]
        builder, loop_state, authored = _builder_with_authoring(protocol, critique)
        original = loop_state.task.spec

        builder._spec_authoring_reentry(loop_state)

        assert loop_state.task.spec == authored
        prov = loop_state.metadata["spec_authoring"]
        assert prov["attempt"] == 1
        assert prov["original"] == original
        assert prov["authored"] == authored


# ---------------------------------------------------------------------------
# A redefinition keeps the evidence the original carried (Fixes #320)
# ---------------------------------------------------------------------------

_EVIDENCE_SPEC = (
    "The crash happens in `packages/app/src/booking.py:42` when the widget "
    "is clicked; test_booking_flow reproduces it."
)


class TestReauthoringKeepsEvidence:
    """A rewrite that leaves the task vaguer than it found it has failed."""

    def _builder_with_spec(self, spec, authored):
        meta_spec = Validator(
            validator_id="meta-spec", validator_type="architecture",
            criteria=["Check spec shape"], judges_spec=True,
        )
        protocol = _protocol([meta_spec])
        builder = GraphBuilder(protocol)
        builder._classifier_completion_fn = MagicMock(
            return_value=_make_response(authored)
        )
        loop_state = LoopState(
            task=Task(id="task_001", spec=spec),
            current_mode="producer",
            needs_spec_authoring=True,
            metadata={"spec_critique": [
                {"validator_id": "meta-spec",
                 "justification": "Spec is code-prescriptive"},
            ]},
        )
        return builder, loop_state

    def test_path_and_line_citation_survives_reauthorship(self):
        """The citation that located the symptom is still in the spec."""
        builder, loop_state = self._builder_with_spec(
            _EVIDENCE_SPEC, "Fix the booking flow."
        )

        builder._spec_authoring_reentry(loop_state)

        assert "packages/app/src/booking.py:42" in loop_state.task.spec
        assert "test_booking_flow" in loop_state.task.spec

    def test_reauthored_spec_not_broader_than_original(self):
        """A generic rewrite is still bounded by the original's evidence."""
        from snodo.engine.evidence import evidence_missing, extract_evidence

        builder, loop_state = self._builder_with_spec(
            _EVIDENCE_SPEC, "Fix the whole application everywhere."
        )

        builder._spec_authoring_reentry(loop_state)

        assert evidence_missing(
            loop_state.task.spec, extract_evidence(_EVIDENCE_SPEC)
        ) == []

    def test_spec_without_evidence_reauthored_unchanged(self):
        """No anchors means the no-evidence path is exactly what it was."""
        builder, loop_state = self._builder_with_spec(
            "Implement feature X", "A clean, well-formed spec."
        )

        builder._spec_authoring_reentry(loop_state)

        assert loop_state.task.spec == "A clean, well-formed spec."

    def test_author_is_told_the_evidence(self):
        """The rewriter prompt names the anchors it must carry forward."""
        builder, loop_state = self._builder_with_spec(
            _EVIDENCE_SPEC, "Fix the booking flow."
        )

        builder._spec_authoring_reentry(loop_state)

        prompt = (
            builder._classifier_completion_fn.call_args[1]["messages"][0]["content"]
        )
        assert "packages/app/src/booking.py:42" in prompt
        assert "test_booking_flow" in prompt

    def test_provenance_records_evidence_restored(self):
        """What the rewrite would have dropped is visible, not silent."""
        builder, loop_state = self._builder_with_spec(
            _EVIDENCE_SPEC, "Fix the booking flow."
        )

        builder._spec_authoring_reentry(loop_state)

        prov = loop_state.metadata["spec_authoring"]
        assert "packages/app/src/booking.py:42" in prov["evidence"]
        assert "packages/app/src/booking.py:42" in prov["evidence_restored"]
        assert "test_booking_flow" in prov["evidence_restored"]

    def test_kept_evidence_is_not_appended_twice(self):
        """An anchor the author kept is not restored again."""
        builder, loop_state = self._builder_with_spec(
            _EVIDENCE_SPEC, "Fix the crash in `packages/app/src/booking.py:42`."
        )

        builder._spec_authoring_reentry(loop_state)

        assert loop_state.task.spec.count("packages/app/src/booking.py:42") == 1
        assert "test_booking_flow" in loop_state.task.spec

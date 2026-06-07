"""Tests for coder respawn on authorized set_model(scope=coder).

W5-05c-2: verified set_model overrides rebuild the coder mid-session.
"""


from snodo.coders import LiteLLMAdapter
from snodo.engine.loop import GraphBuilder


def _make_signing_issuer():
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.backends import default_backend
    from snodo.infrastructure.decisions import SigningDecisionRecordIssuer
    priv = rsa.generate_private_key(65537, 2048, backend=default_backend())
    return SigningDecisionRecordIssuer(priv), priv.public_key()


def _make_verify_issuer(pub):
    from snodo.infrastructure.decisions import VerifyOnlyDecisionRecordIssuer
    return VerifyOnlyDecisionRecordIssuer(pub)


def _make_set_model_jwt(signing_issuer, scope, proposed_model, task_ref="t1"):
    import jwt
    from datetime import datetime, timezone
    payload = {
        "iat": datetime.now(timezone.utc),
        "task_ref": task_ref,
        "type": "set_model",
        "proposed_model": proposed_model,
        "scope": scope,
        "justification": "test",
        "resolved_by": "human",
    }
    return jwt.encode(payload, signing_issuer._private_key, algorithm="RS256")


def _make_builder(coder=None):
    """Create a minimal GraphBuilder with a mock coder."""
    from snodo.compiler.models import Protocol, Mode, Validator
    protocol = Protocol(
        protocol_id="test", name="Test",
        modes=[Mode(mode_id="producer", name="Producer", tools=[], validators=[])],
        validators=[Validator(validator_id="v1", validator_type="security",
                               evaluation_phase="pre_execute")],
        initial_mode="producer",
    )
    if coder is None:
        coder = LiteLLMAdapter(model="claude-sonnet-4-20250514")
    return GraphBuilder(protocol, coder=coder)


class TestCoderRespawn:
    def test_verified_coder_override_respawns(self):
        """A verified coder-scoped set_model rebuilds the coder."""
        signer, pub = _make_signing_issuer()
        verifier = _make_verify_issuer(pub)
        jwt_str = _make_set_model_jwt(signer, "coder", "gemini/gemini-2.0-flash-exp")

        coder = LiteLLMAdapter(model="claude-sonnet-4-20250514")
        builder = _make_builder(coder)
        builder._decision_issuer = verifier
        builder._authorized_decisions = [jwt_str]
        builder.workspace_mcp = None
        old_id = id(builder.coder)

        builder._maybe_respawn_coder()

        assert id(builder.coder) != old_id  # new instance
        assert builder._default_model == "gemini/gemini-2.0-flash-exp"

    def test_all_three_updated_together(self):
        """coder, _completion_fn, _default_model all update on respawn."""
        signer, pub = _make_signing_issuer()
        verifier = _make_verify_issuer(pub)
        jwt_str = _make_set_model_jwt(signer, "coder", "gemini/gemini-2.0-flash-exp")

        coder = LiteLLMAdapter(model="claude-sonnet-4-20250514")
        builder = _make_builder(coder)
        builder._decision_issuer = verifier
        builder._authorized_decisions = [jwt_str]
        builder.workspace_mcp = None

        builder._maybe_respawn_coder()

        assert builder.coder.model == "gemini/gemini-2.0-flash-exp"
        assert builder._completion_fn is not None
        assert builder._default_model == "gemini/gemini-2.0-flash-exp"
        # Validator runner should be in sync
        assert builder._validator_runner._default_model == "gemini/gemini-2.0-flash-exp"
        assert builder._validator_runner._completion_fn is not None

    def test_tampered_override_not_applied(self):
        """A tampered set_model JWT is NOT applied."""
        signer, pub = _make_signing_issuer()
        verifier = _make_verify_issuer(pub)
        jwt_str = _make_set_model_jwt(signer, "coder", "gemini-2.0")
        parts = jwt_str.split(".")
        tampered = f"{parts[0]}.{parts[1] + 'X'}.{parts[2]}"

        coder = LiteLLMAdapter(model="claude-sonnet-4-20250514")
        builder = _make_builder(coder)
        builder._decision_issuer = verifier
        builder._authorized_decisions = [tampered]
        builder.workspace_mcp = None
        old_id = id(builder.coder)

        builder._maybe_respawn_coder()

        assert id(builder.coder) == old_id  # unchanged
        assert builder._default_model == "claude-sonnet-4-20250514"

    def test_same_model_no_respawn(self):
        """Override == current model → no respawn (idempotent)."""
        signer, pub = _make_signing_issuer()
        verifier = _make_verify_issuer(pub)
        jwt_str = _make_set_model_jwt(signer, "coder", "claude-sonnet-4-20250514")

        coder = LiteLLMAdapter(model="claude-sonnet-4-20250514")
        builder = _make_builder(coder)
        builder._decision_issuer = verifier
        builder._authorized_decisions = [jwt_str]
        builder.workspace_mcp = None
        old_id = id(builder.coder)

        builder._maybe_respawn_coder()

        assert id(builder.coder) == old_id  # no new instance needed

    def test_no_override_coder_unchanged(self):
        """No authorized_decisions → coder unchanged."""
        coder = LiteLLMAdapter(model="claude-sonnet-4-20250514")
        builder = _make_builder(coder)
        builder._authorized_decisions = []
        builder._decision_issuer = None
        old_id = id(builder.coder)

        builder._maybe_respawn_coder()

        assert id(builder.coder) == old_id

    def test_validator_scoped_not_applied_to_coder(self):
        """A validator-scoped override does NOT trigger coder respawn."""
        signer, pub = _make_signing_issuer()
        verifier = _make_verify_issuer(pub)
        jwt_str = _make_set_model_jwt(signer, "validator:security", "gpt-4o")

        coder = LiteLLMAdapter(model="claude-sonnet-4-20250514")
        builder = _make_builder(coder)
        builder._decision_issuer = verifier
        builder._authorized_decisions = [jwt_str]
        builder.workspace_mcp = None
        old_id = id(builder.coder)

        builder._maybe_respawn_coder()

        assert id(builder.coder) == old_id

    def test_respawned_coder_has_fresh_max_tool_turns(self):
        """Respawned coder gets a fresh max_tool_turns budget."""
        signer, pub = _make_signing_issuer()
        verifier = _make_verify_issuer(pub)
        jwt_str = _make_set_model_jwt(signer, "coder", "gpt-4o")

        coder = LiteLLMAdapter(model="claude-sonnet-4-20250514", max_tool_turns=3)
        # Simulate using some turns
        coder.max_tool_turns = 1
        builder = _make_builder(coder)
        builder._decision_issuer = verifier
        builder._authorized_decisions = [jwt_str]
        builder.workspace_mcp = None

        builder._maybe_respawn_coder()

        # Fresh instance from config should have default max_tool_turns
        assert builder.coder.max_tool_turns > 1
        assert id(builder.coder) != id(coder)

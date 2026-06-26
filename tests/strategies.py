"""Shared Hypothesis strategies for property-based testing.

FILE: tests/strategies.py (Task 7.16)

Generators for protocols, validators, tokens, results, and other
domain objects used across property tests.
"""

import os

from hypothesis import strategies as st

from snodo.compiler.models import (
    Protocol, Mode, Validator, Severity, DisagreementPolicy,
)
from snodo.core.interfaces import Task, ValidatorResult
from snodo.infrastructure.tokens import TokenIssuer


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

def hypothesis_settings():
    from hypothesis import settings, HealthCheck
    max_examples = 100
    if os.environ.get("SNODO_HYPOTHESIS_LONG") == "1":
        max_examples = 1000
    if os.environ.get("SNODO_HYPOTHESIS_PAPER") == "1":
        max_examples = 10000
    return settings(
        max_examples=max_examples,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )


# ---------------------------------------------------------------------------
# Basic value generators
# ---------------------------------------------------------------------------

severities = st.sampled_from([Severity.PASS, Severity.WARN, Severity.BLOCKER])

severity_strings = st.sampled_from(["pass", "warn", "blocker"])

validator_types = st.sampled_from([
    "security", "architecture", "conventions", "quality", "planning", "protocol",
])

evaluation_phases = st.sampled_from(["pre_execute", "post_execute"])

tool_names = st.sampled_from([
    "edit", "dispatch", "test", "validate", "review", "approve", "merge",
    "pr", "plan", "resolve", "commit",
])

identifiers = st.text(
    alphabet=st.characters(
        whitelist_categories=("Lu", "Ll", "Nd"),
        whitelist_characters=("_",),
    ),
    min_size=3, max_size=20,
).filter(lambda s: s[0].isalpha())


# ---------------------------------------------------------------------------
# ValidatorResult generator
# ---------------------------------------------------------------------------

@st.composite
def validator_results(draw, min_count=1, max_count=5):
    """Generate a list of ValidatorResult objects."""
    n = draw(st.integers(min_count, max_count))
    results = []
    for i in range(n):
        vid = draw(st.from_regex(r"[a-z_]{3,15}"))
        sev = draw(severity_strings)
        just = draw(st.text(min_size=5, max_size=80))
        results.append(ValidatorResult(
            validator_id=vid, severity=sev, justification=just,
        ))
    return results


# ---------------------------------------------------------------------------
# Mode generator — WF1-coherent (disjoint tools)
# ---------------------------------------------------------------------------

@st.composite
def mode_pair_disjoint(draw):
    """Generate two modes with disjoint tool sets (WF1-coherent)."""
    all_tools = draw(st.lists(tool_names, min_size=4, max_size=8, unique=True))
    split = draw(st.integers(1, len(all_tools) - 1))
    t1 = all_tools[:split]
    t2 = all_tools[split:]

    # Validator IDs — shared pool
    v_pool = draw(st.lists(identifiers, min_size=2, max_size=4, unique=True))
    v1 = v_pool[: max(1, len(v_pool) // 2)]
    v2 = v_pool[len(v1):] or v_pool[:1]

    m1 = Mode(
        mode_id="producer", name="Producer Mode",
        tools=t1, validators=v1, transitions={},
    )
    m2 = Mode(
        mode_id="reviewer", name="Reviewer Mode",
        tools=t2, validators=v2, transitions={},
    )
    return m1, m2, v_pool


# ---------------------------------------------------------------------------
# Protocol generator
# ---------------------------------------------------------------------------

@st.composite
def protocols(draw) -> Protocol:
    """Generate a minimal WF1-coherent protocol with 2 modes."""
    m1, m2, v_ids = draw(mode_pair_disjoint())

    validators = []
    for vid in set(v_ids):
        vtype = draw(validator_types)
        phase = draw(evaluation_phases)
        cap = draw(st.one_of(st.none(), st.just(Severity.WARN)))
        validators.append(Validator(
            validator_id=vid, validator_type=vtype,
            evaluation_phase=phase, severity_cap=cap,
        ))

    policy = draw(st.sampled_from([
        DisagreementPolicy.UNANIMOUS, DisagreementPolicy.MAJORITY,
    ]))

    return Protocol(
        protocol_id=draw(identifiers),
        name="Hypothesis Test Protocol",
        version="1.0.0",
        modes=[m1, m2],
        validators=validators,
        disagreement_policy=policy,
        initial_mode="producer",
    )


# ---------------------------------------------------------------------------
# Task generator
# ---------------------------------------------------------------------------

@st.composite
def tasks(draw):
    return Task(
        id=draw(st.text(
            alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd"),
                                     whitelist_characters=("_",)),
            min_size=3, max_size=20,
        ).filter(lambda s: s[0].isalpha() and s.isascii())),
        spec=draw(st.text(
            alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd", "Zs"),
                                     whitelist_characters=("_",)),
            min_size=10, max_size=200,
        )),
    )


# ---------------------------------------------------------------------------
# Token + Issuer generators
# ---------------------------------------------------------------------------

secrets_32 = st.text(
    alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd")),
    min_size=32, max_size=64,
)


@st.composite
def token_issuers(draw):
    secret = draw(secrets_32)
    ttl = draw(st.integers(60, 3600))
    return TokenIssuer(secret=secret, ttl_seconds=ttl)


@st.composite
def jwt_tokens(draw):
    """Generate a valid JWT token for a random task."""
    issuer = draw(token_issuers())
    results = draw(validator_results(min_count=1, max_count=3))
    # Remove blockers so issuance always succeeds
    clean = [r for r in results if r.severity != "blocker"] or [
        ValidatorResult(validator_id="s", severity="pass", justification="ok"),
    ]
    task_id = draw(identifiers)
    token = issuer.issue_token(task_id, clean, "unanimous")
    return token, issuer, task_id


# ---------------------------------------------------------------------------
# Audit log generator
# ---------------------------------------------------------------------------

def gen_audit_events(log, data, min_count=3, max_count=20):
    """Populate an AuditLog instance with random events."""
    event_types = st.sampled_from([
        "governance_check", "validate", "dispatch", "task_complete", "halt",
    ])
    n = data.draw(st.integers(min_count, max_count))
    for _ in range(n):
        etype = data.draw(event_types)
        data_payload = {
            "op": etype,
            "task_ref": data.draw(identifiers),
            "mode": data.draw(st.sampled_from(["producer", "reviewer"])),
        }
        log.append_event(etype, data_payload)
    return log.events

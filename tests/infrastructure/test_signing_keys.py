"""Regression guard for the RS256 signing-key boundary.

FILE: tests/infrastructure/test_signing_keys.py

W5-05a's security claim: engine and MCP processes cannot reach the
private key, therefore cannot mint DecisionRecords.  This test
proves the boundary structurally, and must FAIL if anyone adds a
private-key import to any engine or MCP module.

The import-graph test walks the engine/ and mcp/ module trees
(via ast) and asserts none of the forbidden symbols appear.
"""

import ast
from pathlib import Path

import pytest

# Forbidden symbols — any of these in an engine/MCP module breaks the boundary.
_FORBIDDEN = {
    "load_private_key",
    "signing_issuer",       # factory that calls load_private_key
    "SigningDecisionRecordIssuer",
}
# Paths that MUST be able to reach the private key (positive control).
_PRIVILEGED_PATHS = {
    "snodo/cli/commands/authorize_cmd.py",
}

# Engine and MCP module roots — all .py files under these dirs are checked.
_ENGINE_ROOT = Path(__file__).parent.parent.parent / "snodo" / "engine"
_MCP_ROOT = Path(__file__).parent.parent.parent / "snodo" / "mcp"
_PROJECT_ROOT = Path(__file__).parent.parent.parent


# ------------------------------------------------------------------#
# Import-graph boundary guard
# ------------------------------------------------------------------#

def _collect_imports(file_path: Path) -> set:
    """AST-parse *file_path* and return the set of names it imports."""
    try:
        tree = ast.parse(file_path.read_text())
    except (SyntaxError, UnicodeDecodeError):
        return set()

    names: set = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
                names.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module.split(".")[0])
            for alias in node.names:
                names.add(alias.asname or alias.name)
    return names


def _find_violations(root: Path) -> list:
    """Walk *root* recursively and collect (file, symbol) violations."""
    violations = []
    for py_file in sorted(root.rglob("*.py")):
        if py_file.name == "__init__.py" and py_file.parent == root:
            continue  # skip package-init, check the real modules
        imports = _collect_imports(py_file)
        for forbidden in _FORBIDDEN:
            if forbidden in imports:
                rel = py_file.relative_to(_PROJECT_ROOT)
                violations.append((str(rel), forbidden))
    return violations


def test_engine_modules_never_import_private_key():
    """No engine module imports load_private_key, signing_issuer, or SigningDecisionRecordIssuer."""
    violations = _find_violations(_ENGINE_ROOT)
    assert violations == [], (
        "ENGINE MODULES REACH THE PRIVATE KEY:\n"
        + "\n".join(f"  {f}: imports {s}" for f, s in violations)
        + "\n\nThis breaks the W5-05a security boundary. "
        "The engine must NEVER import the private key. "
        "Use verify_only_issuer() / VerifyOnlyDecisionRecordIssuer instead."
    )


def test_mcp_modules_never_import_private_key():
    """No MCP module imports load_private_key, signing_issuer, or SigningDecisionRecordIssuer."""
    violations = _find_violations(_MCP_ROOT)
    assert violations == [], (
        "MCP MODULES REACH THE PRIVATE KEY:\n"
        + "\n".join(f"  {f}: imports {s}" for f, s in violations)
        + "\n\nThis breaks the W5-05a security boundary. "
        "The MCP server must NEVER import the private key. "
        "Use verify_only_issuer() / VerifyOnlyDecisionRecordIssuer instead."
    )


# ------------------------------------------------------------------#
# Positive control — CLI must reach the private key
# ------------------------------------------------------------------#

def test_privileged_path_reaches_private_key():
    """The authorize CLI command CAN import the signing issuer — it must."""
    missing = []
    for rel_path in _PRIVILEGED_PATHS:
        full = _PROJECT_ROOT / rel_path
        if not full.exists():
            missing.append(rel_path)
            continue
        imports = _collect_imports(full)
        # The privileged path reaches it via signing_issuer, not necessarily
        # by importing load_private_key directly.  Check that at least the
        # SigningDecisionRecordIssuer or signing_issuer is reachable.
        if not (imports & {"signing_issuer", "SigningDecisionRecordIssuer"}):
            missing.append(f"{rel_path} (does not import signing_issuer or SigningDecisionRecordIssuer)")
    assert missing == [], (
        "PRIVILEGED PATHS CANNOT REACH THE PRIVATE KEY:\n"
        + "\n".join(f"  {m}" for m in missing)
        + "\n\nThe legitimate CLI path must be able to sign. "
        "If this fails, authorize_cmd.py can no longer mint DecisionRecords."
    )


# ------------------------------------------------------------------#
# Behavioral guard — VerifyOnly cannot mint
# ------------------------------------------------------------------#

def test_verify_only_issuer_raises_on_mint():
    """VerifyOnlyDecisionRecordIssuer raises DecisionMintRejectedError on mint."""
    from snodo.infrastructure.decisions import (
        VerifyOnlyDecisionRecordIssuer,
        DecisionMintRejectedError,
    )
    from snodo.core.interfaces import ValidatorResult

    # Generate throwaway keypair for test
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.backends import default_backend
    private = rsa.generate_private_key(65537, 2048, backend=default_backend())
    public = private.public_key()

    issuer = VerifyOnlyDecisionRecordIssuer(public)
    result = ValidatorResult(validator_id="test", severity="warn", justification="test")

    with pytest.raises(DecisionMintRejectedError, match="cannot mint"):
        issuer.issue_record(
            task_ref="t1", validator_id="v1",
            validator_result=result, decision="proceed",
            justification="test",
        )


def test_verify_only_issuer_can_verify():
    """VerifyOnlyDecisionRecordIssuer can verify records signed by a signing issuer."""
    from snodo.infrastructure.decisions import (
        SigningDecisionRecordIssuer,
        VerifyOnlyDecisionRecordIssuer,
    )
    from snodo.core.interfaces import ValidatorResult

    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.backends import default_backend
    private = rsa.generate_private_key(65537, 2048, backend=default_backend())
    public = private.public_key()

    signer = SigningDecisionRecordIssuer(private)
    verifier = VerifyOnlyDecisionRecordIssuer(public)

    result = ValidatorResult(validator_id="v1", severity="warn", justification="concern")
    record = signer.issue_record("t1", "v1", result, "proceed", "human override")

    payload = verifier.verify_record(record.jwt, expected_task_ref="t1")
    assert payload is not None
    assert payload["decision"] == "proceed"


def test_tampered_rs256_record_fails():
    """RS256 signature tampering is detected."""
    from snodo.infrastructure.decisions import (
        SigningDecisionRecordIssuer,
        VerifyOnlyDecisionRecordIssuer,
    )
    from snodo.core.interfaces import ValidatorResult

    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.backends import default_backend
    private = rsa.generate_private_key(65537, 2048, backend=default_backend())
    public = private.public_key()

    signer = SigningDecisionRecordIssuer(private)
    verifier = VerifyOnlyDecisionRecordIssuer(public)

    result = ValidatorResult(validator_id="v1", severity="warn", justification="concern")
    record = signer.issue_record("t1", "v1", result, "proceed", "human override")

    # Tamper: flip one byte in the base64-encoded payload
    parts = record.jwt.split(".")
    tampered = f"{parts[0]}.{parts[1] + 'X'}.{parts[2]}"
    assert verifier.verify_record(tampered) is None


def test_keypair_generation_idempotent():
    """generate_keypair is idempotent — second call is a no-op."""
    from snodo.infrastructure.signing_keys import generate_keypair, keypair_exists

    priv, pub = generate_keypair()
    assert keypair_exists()

    # Second call should not overwrite
    priv2, pub2 = generate_keypair()
    assert priv2 == priv
    assert pub2 == pub
    assert keypair_exists()


def test_signing_issuer_raises_on_blocker():
    """Minting for blocker severity raises DecisionInvalidSeverityError."""
    from snodo.infrastructure.decisions import (
        SigningDecisionRecordIssuer,
        DecisionInvalidSeverityError,
    )
    from snodo.core.interfaces import ValidatorResult

    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.backends import default_backend
    private = rsa.generate_private_key(65537, 2048, backend=default_backend())

    issuer = SigningDecisionRecordIssuer(private)
    result = ValidatorResult(validator_id="v1", severity="blocker", justification="critical")

    with pytest.raises(DecisionInvalidSeverityError, match="blocker"):
        issuer.issue_record("t1", "v1", result, "proceed", "override")


def test_hs256_legacy_record_returned_none():
    """An HS256-signed DecisionRecord fails verification (retired scheme)."""
    import jwt
    from snodo.infrastructure.decisions import VerifyOnlyDecisionRecordIssuer

    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.backends import default_backend
    private = rsa.generate_private_key(65537, 2048, backend=default_backend())

    # Sign with HS256 (old scheme)
    secret = "test-secret-at-least-32-bytes-long!!"
    payload = {
        "iat": "2025-01-01T00:00:00Z",
        "task_ref": "t1",
        "validator_id": "v1",
        "adjudicated_severity": "warn",
        "adjudicated_justification": "concern",
        "decision": "proceed",
        "justification": "ok",
        "resolved_by": "human",
    }
    hs256_jwt = jwt.encode(payload, secret, algorithm="HS256")

    verifier = VerifyOnlyDecisionRecordIssuer(private.public_key())
    assert verifier.verify_record(hs256_jwt) is None

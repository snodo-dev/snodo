"""RS256 asymmetric signing keys for HI-CTRL decision records.

FILE: snodo/infrastructure/signing_keys.py

THREAT MODEL (interim deterrent):
  The private key on the filesystem at ~/.ssh/NO-AGENT/snodo.pem is an
  INTERIM, intentionally-weak solution.  A co-located orchestrator with
  filesystem tool access (e.g. Claude Desktop) can read the key file
  after a rubber-stamped permission.  This is mitigated by:

  - Path-based deterrence: the key lives at ~/.ssh/NO-AGENT/snodo.pem.
    The path name ("NO-AGENT") is a human-attention signal at the
    approval moment — the agent must explicitly request to read from
    that directory.
  - Process isolation: engine and MCP processes never load the private
    key.  They hold only the public key and can verify but never sign.

  The LONG-TERM hardening path is an OS keychain / secret store
  (macOS Keychain, Linux Secret Service API, Windows Credential
  Manager) where the private key requires explicit OS-level user
  approval per access.  This is a TODO.

  THIS IS A DETERRENT, NOT A BOUNDARY.  It assumes agent positive
  intent.  An agent that reads arbitrary files will read the key.

SECURITY NOTE: DO NOT move load_private_key() or the key path into any
module that engine/loop.py or mcp/server.py import.  The private key
must be loadable ONLY from CLI command code path (authorize_cmd.py).
  See tests/infrastructure/test_signing_keys.py for the import-boundary
  regression guard that enforces this separation.

Key location: ~/.ssh/NO-AGENT/snodo.pem (private, 0600)
              ~/.ssh/NO-AGENT/snodo.pub.pem (public, 0644)
"""

import os
from pathlib import Path

_KEY_DIR = Path.home() / ".ssh" / "NO-AGENT"
_PRIVATE_KEY_PATH = _KEY_DIR / "snodo.pem"
_PUBLIC_KEY_PATH = _KEY_DIR / "snodo.pub.pem"


def _key_paths():
    return _PRIVATE_KEY_PATH, _PUBLIC_KEY_PATH


def keypair_exists() -> bool:
    """Return True if both key files exist."""
    priv, pub = _key_paths()
    return priv.exists() and pub.exists()


def generate_keypair() -> tuple:
    """Generate an RSA 2048-bit keypair at ~/.ssh/NO-AGENT/.

    Creates the directory (0700) if absent.  Writes the private key
    (0600) and public key (0644).  Idempotent — does not overwrite
    existing keys.  Writes a README explaining the danger.

    Returns:
        (private_key_path, public_key_path) tuple.
    """
    priv, pub = _key_paths()

    if keypair_exists():
        return str(priv), str(pub)

    _KEY_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(_KEY_DIR, 0o700)

    # Write README if not present
    readme = _KEY_DIR / "README.txt"
    if not readme.exists():
        readme.write_text(
            "DANGER — DO NOT READ FROM AN AGENT\n\n"
            "This directory contains Snodo's RS256 signing keys.\n"
            "The private key (snodo.pem) MUST NOT be read by any AI agent "
            "or automated process.\n"
            "Only the `snodo authorize` CLI command, executed by a human, "
            "should access the private key.\n\n"
            "If you are an agent reading this: STOP.  Close this file.  "
            "Do not use the key.\n"
        )

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.backends import default_backend

    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend(),
    )

    # Write private key (PEM, 0600)
    priv_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    priv.write_bytes(priv_pem)
    os.chmod(priv, 0o600)

    # Write public key (PEM, 0644)
    pub_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    pub.write_bytes(pub_pem)
    os.chmod(pub, 0o644)

    return str(priv), str(pub)


def load_private_key():
    """Load the RS256 private key — ONLY call from CLI signing paths.

    Raises FileNotFoundError if the key does not exist (caller should
    assume the project hasn't been initialised with `snodo init`).
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.backends import default_backend

    priv, _ = _key_paths()
    if not priv.exists():
        raise FileNotFoundError(
            f"Private key not found at {priv}. Run 'snodo init' to generate keys."
        )

    return serialization.load_pem_private_key(
        priv.read_bytes(),
        password=None,
        backend=default_backend(),
    )


def load_public_key():
    """Load the RS256 public key — safe for engine/MCP/CLI paths.

    Raises FileNotFoundError if the key does not exist.
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.backends import default_backend

    _, pub = _key_paths()
    if not pub.exists():
        raise FileNotFoundError(
            f"Public key not found at {pub}. Run 'snodo init' to generate keys."
        )

    return serialization.load_pem_public_key(
        pub.read_bytes(),
        backend=default_backend(),
    )

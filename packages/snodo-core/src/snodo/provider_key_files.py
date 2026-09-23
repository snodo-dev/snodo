"""Local, reversible encryption for provider credentials (not a secret vault)."""

import os
import re
from functools import lru_cache
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


KEY_NAME = "provider-keys.pem"
PUBLIC_NAME = "provider-keys.pub.pem"


def key_directory() -> Path:
    return Path.home() / ".ssh" / "NO-AGENT"


def provider_file(name: str) -> Path:
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name) or name in (".", ".."):
        raise ValueError("Invalid provider name for encrypted key file")
    return key_directory() / "keys" / f"{name}.key"


def _oaep() -> padding.OAEP:
    return padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None)


def _write_new(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def public_key():
    """Create a dedicated keypair only if neither half exists."""
    directory = key_directory()
    private_path = directory / KEY_NAME
    public_path = directory / PUBLIC_NAME
    if not private_path.exists() and not public_path.exists():
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory.chmod(0o700)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        _write_new(private_path, key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
        ))
        _write_new(public_path, key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
        ))
    if not private_path.is_file() or not public_path.is_file():
        raise ValueError("Incomplete provider encryption keypair in ~/.ssh/NO-AGENT")
    return serialization.load_pem_public_key(public_path.read_bytes())


def encrypt(name: str, value: str) -> None:
    path = provider_file(name)
    key = public_key()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    _write_new(path, key.encrypt(value.encode("utf-8"), _oaep()))


@lru_cache(maxsize=1)
def _private_key(path: Path):
    return serialization.load_pem_private_key(path.read_bytes(), password=None)


@lru_cache(maxsize=128)
def decrypt(path: Path) -> str:
    key = _private_key(key_directory() / KEY_NAME)
    return key.decrypt(path.read_bytes(), _oaep()).decode("utf-8")

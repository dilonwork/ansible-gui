"""Encryption at rest for secrets (SSH private keys).

Fernet symmetric encryption. The key comes from the ENCRYPTION_KEY env var;
for local dev without one, a key is generated once and kept in
./.encryption_key (0600, gitignored).

Production note (M7): the key must come from a real secret manager
(Vault / KMS), not a compose file. Rotating the key without re-encrypting
existing rows makes them unreadable.
"""
import os
import stat
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

_KEY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "..", ".encryption_key")


def _load_key() -> bytes:
    env_key = os.environ.get("ENCRYPTION_KEY")
    if env_key:
        return env_key.encode()
    if os.path.exists(_KEY_FILE):
        with open(_KEY_FILE, "rb") as f:
            return f.read().strip()
    key = Fernet.generate_key()
    with open(_KEY_FILE, "wb") as f:
        f.write(key)
    os.chmod(_KEY_FILE, stat.S_IRUSR | stat.S_IWUSR)
    return key


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    return Fernet(_load_key())


def encrypt_str(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_str(ciphertext: str) -> str:
    """Decrypt; legacy plaintext rows (pre-encryption) pass through so the
    upgrade does not break existing databases. They get re-encrypted on the
    next write."""
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        return ciphertext

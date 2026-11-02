"""Security primitives: password hashing, session tokens, secret encryption.

Third-party crypto imports are deliberately inside functions so that pure
logic tests can run without the `cryptography` wheel installed.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets

from . import config

_SCRYPT_N = 2 ** 14
_SCRYPT_R = 8
_SCRYPT_P = 1

# ------------------------------------------------------------------ passwords

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(
        password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R,
        p=_SCRYPT_P, dklen=32,
    )
    return f"$scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, algo, n, r, p, salt_hex, dk_hex = stored.split("$")
        if algo != "scrypt":
            return False
        dk = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt_hex),
            n=int(n), r=int(r), p=int(p), dklen=32,
        )
        return hmac.compare_digest(dk.hex(), dk_hex)
    except (ValueError, TypeError):
        return False


# ------------------------------------------------------------------ sessions

def new_session_token() -> tuple[str, str]:
    """Return (raw_token, sha256_hash). Store only the hash."""
    raw = secrets.token_urlsafe(32)
    return raw, hashlib.sha256(raw.encode()).hexdigest()


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


# ------------------------------------------------------------------ encryption
# Data encryption lives in the DB; key file lives next to it. If the key file
# is lost (ephemeral disk), saved secrets are unrecoverable — documented.

def _secret_key() -> bytes:
    if config.SECRET_PATH.exists():
        return bytes.fromhex(config.SECRET_PATH.read_text().strip())
    key = os.urandom(32)
    config.SECRET_PATH.write_text(key.hex())
    return key


def encrypt_secret(plaintext: str) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if not plaintext:
        return ""
    aes = AESGCM(_secret_key())
    nonce = os.urandom(12)
    ct = aes.encrypt(nonce, plaintext.encode(), None)
    return "v1:" + base64.b64encode(nonce + ct).decode()


def decrypt_secret(stored: str) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if not stored:
        return ""
    if not stored.startswith("v1:"):
        return stored
    raw = base64.b64decode(stored[3:])
    aes = AESGCM(_secret_key())
    return aes.decrypt(raw[:12], raw[12:], None).decode()

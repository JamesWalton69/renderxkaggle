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
# Key resolution order: SECRET_KEY env var -> Neon Postgres settings table -> local file.
# Survives Render redeploys because it is persisted in Neon.

_cached_key: bytes | None = None


def _secret_key() -> bytes:
    global _cached_key
    if _cached_key is not None:
        return _cached_key
    env_k = os.environ.get("SECRET_KEY", "").strip()
    if env_k:
        try:
            _cached_key = bytes.fromhex(env_k) if len(env_k) == 64 else hashlib.sha256(env_k.encode()).digest()
            return _cached_key
        except Exception:
            pass
    try:
        from . import db
        stored = db.get_setting("app_secret_key", "")
        if stored:
            _cached_key = bytes.fromhex(stored)
            return _cached_key
        new_key = os.urandom(32)
        db.set_setting("app_secret_key", new_key.hex())
        _cached_key = new_key
        return _cached_key
    except Exception:
        pass
    if config.SECRET_PATH.exists():
        _cached_key = bytes.fromhex(config.SECRET_PATH.read_text().strip())
        return _cached_key
    key = os.urandom(32)
    try:
        config.SECRET_PATH.write_text(key.hex())
    except Exception:
        pass
    _cached_key = key
    return _cached_key


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

"""Credential primitives: password hashing (PBKDF2), API keys, session tokens.

Only hashes are ever persisted; plaintext API keys are shown once at mint time
and session tokens live in the browser cookie.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

API_KEY_PREFIX = "sb_"
_PBKDF2_ITERATIONS = 200_000


# -- passwords (dashboard admins) ---------------------------------------------

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITERATIONS)
    return f"pbkdf2${_PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iters, salt_hex, digest_hex = stored.split("$")
        if scheme != "pbkdf2":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt_hex), int(iters))
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


# -- API keys (per-service) ---------------------------------------------------

def new_api_key() -> tuple[str, str]:
    """Returns (plaintext, sha256 hash). Plaintext is never stored."""
    key = API_KEY_PREFIX + secrets.token_hex(24)
    return key, hash_api_key(key)


def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


# -- session tokens (dashboard cookies) ---------------------------------------

def new_session_token() -> tuple[str, str]:
    """Returns (plaintext token, sha256 hash)."""
    token = secrets.token_urlsafe(32)
    return token, hashlib.sha256(token.encode()).hexdigest()


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()

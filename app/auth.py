"""Authentication: password hashing and JWTs with server-side revocation."""

import hashlib
import hmac
import os
import uuid
from datetime import UTC, datetime, timedelta

import jwt

JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret-do-not-use-in-production")
JWT_ALGORITHM = "HS256"
TOKEN_TTL_MINUTES = int(os.getenv("TOKEN_TTL_MINUTES", "60"))

# PBKDF2 rather than a bare hash: cheap to use, and deliberately slow to
# brute-force. Not bcrypt/argon2 only to keep this repo dependency-light —
# a real service should use argon2.
_PBKDF2_ROUNDS = 120_000


def hash_password(password: str, *, salt: str | None = None) -> str:
    salt = salt or uuid.uuid4().hex
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), _PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${_PBKDF2_ROUNDS}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, rounds, salt, expected = stored.split("$")
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(rounds))
    # Constant-time compare so a timing signal can't leak the hash.
    return hmac.compare_digest(digest.hex(), expected)


def create_token(user_id: str, username: str) -> tuple[str, str]:
    """Returns (token, jti). The jti is what logout revokes."""
    jti = uuid.uuid4().hex
    now = datetime.now(UTC)
    payload = {
        "sub": user_id,
        "username": username,
        "jti": jti,
        "iat": now,
        "exp": now + timedelta(minutes=TOKEN_TTL_MINUTES),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM), jti


def decode_token(token: str) -> dict:
    """Raises jwt.PyJWTError on anything malformed, expired or mis-signed."""
    return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])

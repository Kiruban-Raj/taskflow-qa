"""
Authentication - proving who someone is, and keeping their password safe.

WHAT THIS MODULE NEEDS TO ACHIEVE
    Two jobs, both security-sensitive:

      1. Store passwords so that stealing the database does not hand an
         attacker everybody's password.
      2. Issue and check tokens, so a user proves who they are once at login
         rather than sending their password on every single request.

HOW IT ACHIEVES IT
    Passwords go through PBKDF2 with a per-user random salt. Tokens are JWTs
    carrying the user, their tenant, and a unique token id used for logout.

    Note what is NOT in here: no database access and no HTTP. This module is
    pure functions over strings, which means every rule in it can be tested
    without starting anything up.
"""

import hashlib
import hmac
import os
import uuid
from datetime import UTC, datetime, timedelta

import jwt

JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret-do-not-use-in-production")
JWT_ALGORITHM = "HS256"
TOKEN_TTL_MINUTES = int(os.getenv("TOKEN_TTL_MINUTES", "60"))

# Why this number: hashing needs to be slow. A fast hash lets an attacker who
# steals the database try billions of guesses per second. 120,000 rounds makes
# each guess expensive for them, while costing us a few milliseconds at login.
#
# Why PBKDF2 rather than argon2/bcrypt: only to keep this project free of
# compiled dependencies. A real service should prefer argon2.
_PBKDF2_ROUNDS = 120_000


def hash_password(password: str, *, salt: str | None = None) -> str:
    """Turn a password into something safe to store.

    HOW
        A random salt is generated per user and mixed into the hash. That is
        what stops two users who happen to pick the same password from
        having identical hashes, and it defeats precomputed rainbow tables.

    WHAT IS RETURNED
        One string holding the algorithm, the cost, the salt and the digest,
        separated by '$'. Keeping the parameters next to the hash means we
        can raise the cost later and still verify old passwords - each
        stored hash remembers how it was made.
    """
    salt = salt or uuid.uuid4().hex
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), _PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${_PBKDF2_ROUNDS}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Check a submitted password against a stored hash.

    HOW
        Pull the salt and cost back out of the stored string, hash the
        candidate the same way, and compare the results.

    THE DETAIL THAT MATTERS
        The comparison uses hmac.compare_digest, not `==`. A normal string
        comparison stops as soon as it finds a difference, so it takes very
        slightly longer the more leading characters are correct. Measured
        across many attempts, that timing difference leaks the hash one
        character at a time. compare_digest always takes the same time.

        A malformed stored value returns False rather than raising: a
        corrupt row should fail that login, not crash the endpoint.
    """
    try:
        algorithm, rounds, salt, expected = stored.split("$")
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False

    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(rounds))
    return hmac.compare_digest(digest.hex(), expected)


def create_token(user_id: str, username: str, tenant_id: str) -> tuple[str, str]:
    """Issue a signed token for a user who has just proved who they are.

    WHAT GOES INSIDE, AND WHY EACH FIELD EARNS ITS PLACE
        sub      - the user id. Who this is.
        username - convenience, so /me needs no database lookup.
        tenant   - which customer this token belongs to. This is what lets
                   the server reject a Customer A token presented on
                   Customer B's hostname, rather than trusting the URL alone.
        jti      - a unique id for this particular token. Logout records it
                   as revoked; without it we could only cancel all of a
                   user's tokens at once, never a single device.
        iat/exp  - issued-at and expiry, so a stolen token stops working by
                   itself after TOKEN_TTL_MINUTES.

    Returns the token AND its jti, because the caller sometimes needs the id
    without decoding what it has just created.
    """
    jti = uuid.uuid4().hex
    now = datetime.now(UTC)
    payload = {
        "sub": user_id,
        "username": username,
        "tenant": tenant_id,
        "jti": jti,
        "iat": now,
        "exp": now + timedelta(minutes=TOKEN_TTL_MINUTES),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM), jti


def decode_token(token: str) -> dict:
    """Verify a token's signature and expiry, and hand back what it claims.

    Raises jwt.PyJWTError (or a subclass such as ExpiredSignatureError) for
    anything malformed, expired, or signed with the wrong key.

    Those errors are deliberately NOT caught here. The HTTP layer decides
    what each failure should look like to the caller; this module has no
    business inventing status codes.
    """
    return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])

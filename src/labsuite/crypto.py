"""Self-contained credential + session-token primitives (stdlib only).

The Okta layer needs to (a) store passwords safely and (b) hand out a signed
session token after a successful login. Real Okta issues OIDC/JWT tokens signed
with RSA and stores passwords with a slow KDF; we reproduce the *shape* of both
with nothing but the Python standard library so LabSuite runs anywhere:

* passwords  -> PBKDF2-HMAC-SHA256 with a per-user random salt
* tokens     -> a compact HS256 JWT (header.payload.signature, base64url)

Swap these two functions for a real KDF / a real asymmetric JWT library and the
rest of the control plane is unchanged.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

_PBKDF2_ITERATIONS = 200_000
_PBKDF2_ALG = "pbkdf2_sha256"


class TokenError(Exception):
    """Raised when a session token is malformed, tampered with, or expired."""


# --------------------------------------------------------------------------- #
# Passwords
# --------------------------------------------------------------------------- #
def hash_password(password: str, *, salt: bytes | None = None) -> str:
    """Return a ``pbkdf2_sha256$iterations$salt$hash`` string for ``password``."""
    if salt is None:
        salt = secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return f"{_PBKDF2_ALG}${_PBKDF2_ITERATIONS}${salt.hex()}${derived.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time check of ``password`` against a :func:`hash_password` string."""
    try:
        alg, iterations, salt_hex, hash_hex = stored.split("$")
    except ValueError:
        return False
    if alg != _PBKDF2_ALG:
        return False
    derived = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations)
    )
    return hmac.compare_digest(derived.hex(), hash_hex)


# --------------------------------------------------------------------------- #
# Tokens (compact HS256 JWT)
# --------------------------------------------------------------------------- #
def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def sign_token(claims: dict, secret: str, *, ttl_seconds: int = 3600, now: float | None = None) -> str:
    """Sign ``claims`` into an HS256 JWT with an ``exp`` ``ttl_seconds`` out."""
    issued = int(now if now is not None else time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    body = {**claims, "iat": issued, "exp": issued + ttl_seconds}
    header_b64 = _b64url_encode(json.dumps(header, separators=(",", ":")).encode())
    body_b64 = _b64url_encode(json.dumps(body, separators=(",", ":")).encode())
    signing_input = f"{header_b64}.{body_b64}".encode()
    signature = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    return f"{header_b64}.{body_b64}.{_b64url_encode(signature)}"


def verify_token(token: str, secret: str, *, now: float | None = None) -> dict:
    """Verify signature + expiry and return the claims, or raise :class:`TokenError`."""
    try:
        header_b64, body_b64, signature_b64 = token.split(".")
    except ValueError as exc:
        raise TokenError("malformed token") from exc

    signing_input = f"{header_b64}.{body_b64}".encode()
    expected = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    if not hmac.compare_digest(expected, _b64url_decode(signature_b64)):
        raise TokenError("bad signature")

    claims = json.loads(_b64url_decode(body_b64))
    current = now if now is not None else time.time()
    if current >= claims.get("exp", 0):
        raise TokenError("token expired")
    return claims

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from jwt import InvalidTokenError


_PASSWORD_SCHEME = "pbkdf2_sha256"
_PASSWORD_ITERATIONS = 310_000


class TokenDecodeError(ValueError):
    """Raised when an access token cannot be trusted."""


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def hash_password(password: str) -> str:
    if len(password) < 8:
        raise ValueError("password must contain at least 8 characters")
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, _PASSWORD_ITERATIONS
    )
    return "$".join(
        (
            _PASSWORD_SCHEME,
            str(_PASSWORD_ITERATIONS),
            _b64encode(salt),
            _b64encode(digest),
        )
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, iterations_raw, salt_raw, expected_raw = encoded.split("$", 3)
        if scheme != _PASSWORD_SCHEME:
            return False
        iterations = int(iterations_raw)
        if iterations <= 0 or iterations > 2_000_000:
            return False
        salt = _b64decode(salt_raw)
        expected = _b64decode(expected_raw)
    except (ValueError, TypeError):
        return False

    actual = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, iterations
    )
    return hmac.compare_digest(actual, expected)


def create_access_token(
    *,
    user_id: int,
    email: str,
    secret_key: str,
    expires_minutes: int,
) -> str:
    if user_id <= 0:
        raise ValueError("user_id must be positive")
    if not secret_key:
        raise ValueError("auth secret key must not be empty")
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "email": email,
        "iat": now,
        "exp": now + timedelta(minutes=expires_minutes),
        "type": "access",
    }
    return jwt.encode(payload, secret_key, algorithm="HS256")


def decode_access_token(*, token: str, secret_key: str) -> tuple[int, str]:
    try:
        payload = jwt.decode(token, secret_key, algorithms=["HS256"])
        if payload.get("type") != "access":
            raise TokenDecodeError("invalid token type")
        user_id = int(payload["sub"])
        email = str(payload.get("email") or "").strip().lower()
        if user_id <= 0 or not email:
            raise TokenDecodeError("token identity is incomplete")
        return user_id, email
    except (InvalidTokenError, KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, TokenDecodeError):
            raise
        raise TokenDecodeError("invalid or expired access token") from exc

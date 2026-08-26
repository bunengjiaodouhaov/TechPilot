import pytest

from app.auth.security import (
    TokenDecodeError,
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


def test_password_hash_is_salted_and_verifiable() -> None:
    first = hash_password("correct-horse")
    second = hash_password("correct-horse")

    assert first != second
    assert verify_password("correct-horse", first) is True
    assert verify_password("wrong-password", first) is False


def test_access_token_round_trip_binds_identity() -> None:
    token = create_access_token(
        user_id=7,
        email="user@example.com",
        secret_key="test-secret",
        expires_minutes=5,
    )

    assert decode_access_token(
        token=token,
        secret_key="test-secret",
    ) == (7, "user@example.com")


def test_access_token_rejects_wrong_signing_key() -> None:
    token = create_access_token(
        user_id=7,
        email="user@example.com",
        secret_key="test-secret",
        expires_minutes=5,
    )

    with pytest.raises(TokenDecodeError):
        decode_access_token(token=token, secret_key="different-secret")

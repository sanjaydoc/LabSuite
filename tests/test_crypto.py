"""Credential + token primitives."""

import pytest

from labsuite.crypto import (
    TokenError,
    hash_password,
    sign_token,
    verify_password,
    verify_token,
)


def test_password_roundtrip():
    stored = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", stored)
    assert not verify_password("wrong password", stored)


def test_password_hash_is_salted():
    # Same password hashed twice yields different strings (random salt).
    assert hash_password("hunter2") != hash_password("hunter2")


def test_verify_password_rejects_garbage():
    assert not verify_password("x", "not-a-valid-hash")


def test_token_roundtrip():
    token = sign_token({"sub": "anguyen"}, "secret", ttl_seconds=100, now=1000)
    claims = verify_token(token, "secret", now=1050)
    assert claims["sub"] == "anguyen"
    assert claims["exp"] == 1100


def test_token_expired():
    token = sign_token({"sub": "x"}, "secret", ttl_seconds=10, now=1000)
    with pytest.raises(TokenError):
        verify_token(token, "secret", now=2000)


def test_token_tampered_signature():
    token = sign_token({"sub": "x"}, "secret", now=1000)
    with pytest.raises(TokenError):
        verify_token(token, "different-secret", now=1000)


def test_token_malformed():
    with pytest.raises(TokenError):
        verify_token("not.a.jwt.at.all", "secret")

"""Focused unit tests for ApplyTrack Security Core.

Covers:
1. Explicit Argon2id configuration (Type.ID, time_cost=2, memory_cost=65536, parallelism=1).
2. Argon2id password hashing and parameter encoding.
3. Correct password verification.
4. Incorrect password verification failure.
5. Malformed/corrupted hash error safety.
6. Empty or invalid input handling.
7. Cryptographically secure session token generation, format, and entropy.
8. SHA-256 session token hashing (64-character hexadecimal digests).
9. Distinct session tokens produce distinct digests.
10. Dummy hash constant format, Argon2id parameters, and static precomputation.
11. Dummy verification functionality and safe rejection.
12. Dummy verification reuses the precomputed hash without calling hash() on each call.
13. Verification of session and cookie settings in app.config.
"""

import string
from argon2 import Type
import pytest

from app.config import Settings, settings
from app.core import security
from app.core.security import (
    _DUMMY_HASH,
    dummy_verify_password,
    generate_session_token,
    hash_password,
    hash_session_token,
    verify_password,
)


def test_hasher_explicitly_configured_with_argon2id():
    """Verify that the PasswordHasher instance is explicitly configured with Type.ID and approved parameters."""
    assert security._hasher.type == Type.ID
    assert security._hasher.time_cost == 2
    assert security._hasher.memory_cost == 65536
    assert security._hasher.parallelism == 1


def test_hash_password_uses_argon2id():
    """Verify generated password hashes use Argon2id with configured parameters."""
    pwd = "CorrectHorseBatteryStaple123!"
    encoded_hash = hash_password(pwd)

    # Must start with $argon2id$
    assert encoded_hash.startswith("$argon2id$")

    # Must include OWASP target parameters (time_cost=2, memory_cost=65536, parallelism=1)
    assert "m=65536" in encoded_hash
    assert "t=2" in encoded_hash
    assert "p=1" in encoded_hash


def test_verify_password_correct():
    """Verify that the correct plain-text password matches the generated hash."""
    pwd = "UltraSecurePassword987!"
    encoded_hash = hash_password(pwd)

    assert verify_password(pwd, encoded_hash) is True


def test_verify_password_incorrect():
    """Verify that an incorrect password fails verification."""
    pwd = "UltraSecurePassword987!"
    encoded_hash = hash_password(pwd)

    assert verify_password("WrongPassword123!", encoded_hash) is False


def test_verify_password_malformed_hashes():
    """Verify that malformed or corrupted hashes fail safely without raising exceptions."""
    pwd = "SomePassword123!"

    malformed_hashes = [
        "not-a-valid-hash",
        "$argon2id$v=19$corrupted_payload",
        "1234567890",
        "$pbkdf2-sha256$29000$bad",
        "!!!",
    ]

    for bad_hash in malformed_hashes:
        assert verify_password(pwd, bad_hash) is False


def test_empty_and_invalid_inputs():
    """Verify that empty or non-string inputs are handled safely and predictably."""
    # hash_password requires a valid non-empty string
    with pytest.raises(ValueError, match="Password must be a non-empty string"):
        hash_password("")

    with pytest.raises(ValueError, match="Password must be a non-empty string"):
        hash_password(None)  # type: ignore

    with pytest.raises(ValueError, match="Password must be a non-empty string"):
        hash_password(12345)  # type: ignore

    # verify_password safely returns False for empty or invalid inputs
    valid_hash = hash_password("ValidPassword123!")
    assert verify_password("", valid_hash) is False
    assert verify_password(None, valid_hash) is False  # type: ignore
    assert verify_password("ValidPassword123!", "") is False
    assert verify_password("ValidPassword123!", None) is False  # type: ignore

    # hash_session_token requires a non-empty string
    with pytest.raises(ValueError, match="Session token must be a non-empty string"):
        hash_session_token("")

    with pytest.raises(ValueError, match="Session token must be a non-empty string"):
        hash_session_token(None)  # type: ignore


def test_generate_session_token_entropy_and_format():
    """Verify generated session tokens have high entropy, URL-safe format, and sufficient length."""
    tokens = [generate_session_token() for _ in range(100)]

    # Every token must be unique (256 bits of entropy prevents collisions)
    assert len(set(tokens)) == 100

    url_safe_chars = set(string.ascii_letters + string.digits + "-_")
    for token in tokens:
        # Base64-encoded 32 bytes yields 43 characters
        assert len(token) >= 43
        assert set(token).issubset(url_safe_chars)


def test_hash_session_token_format_and_length():
    """Verify hash_session_token produces a 64-character hexadecimal SHA-256 digest."""
    token = generate_session_token()
    digest = hash_session_token(token)

    assert len(digest) == 64
    # Must be valid hexadecimal
    int(digest, 16)
    assert digest == digest.lower()


def test_different_session_tokens_produce_different_digests():
    """Verify distinct session tokens produce distinct digests and hashing is deterministic."""
    t1 = generate_session_token()
    t2 = generate_session_token()

    h1 = hash_session_token(t1)
    h2 = hash_session_token(t2)

    assert h1 != h2
    # Deterministic hashing: same input always produces same digest
    assert hash_session_token(t1) == h1


def test_dummy_hash_uses_argon2id_with_approved_parameters():
    """Verify that the precomputed dummy hash constant is an Argon2id hash with approved parameters."""
    # Must be a non-empty string constant starting with $argon2id$
    assert isinstance(_DUMMY_HASH, str)
    assert _DUMMY_HASH.startswith("$argon2id$")
    assert "m=65536" in _DUMMY_HASH
    assert "t=2" in _DUMMY_HASH
    assert "p=1" in _DUMMY_HASH


def test_dummy_verify_password_works():
    """Verify dummy verification runs successfully against the precomputed hash and always returns False."""
    assert dummy_verify_password("any_unrecognized_password") is False
    assert dummy_verify_password("") is False
    assert dummy_verify_password(None) is False  # type: ignore


def test_dummy_verify_does_not_generate_new_hash_on_each_call(monkeypatch):
    """Verify dummy verification verifies against the precomputed hash and never calls hash()."""
    class HasherSpy:
        def __init__(self, real_hasher):
            self._real = real_hasher
            self.hash_call_count = 0
            self.verify_call_count = 0

        def hash(self, *args, **kwargs):
            self.hash_call_count += 1
            return self._real.hash(*args, **kwargs)

        def verify(self, *args, **kwargs):
            self.verify_call_count += 1
            return self._real.verify(*args, **kwargs)

    spy = HasherSpy(security._hasher)
    monkeypatch.setattr(security, "_hasher", spy)

    # Perform dummy verification calls
    res1 = dummy_verify_password("user_candidate_password_1")
    res2 = dummy_verify_password("user_candidate_password_2")

    assert res1 is False
    assert res2 is False

    # PasswordHasher.hash() must NOT have been called during dummy verification
    assert spy.hash_call_count == 0

    # PasswordHasher.verify() must have been called exactly twice against the precomputed hash
    assert spy.verify_call_count == 2


def test_config_session_and_cookie_settings():
    """Verify Phase 2 session and cookie settings in app.config."""
    assert settings.SESSION_IDLE_TIMEOUT_MINUTES == 120
    assert settings.SESSION_ABSOLUTE_LIFETIME_HOURS == 24
    assert settings.SESSION_COOKIE_NAME == "applytrack_session"
    assert settings.SESSION_COOKIE_HTTPONLY is True
    assert settings.SESSION_COOKIE_SAMESITE == "lax"
    assert settings.SESSION_COOKIE_PATH == "/"
    # In local development, secure is False
    assert settings.SESSION_COOKIE_SECURE is False

    # In production, secure dynamically defaults to True
    prod_settings = Settings(
        ENVIRONMENT="production",
        DATABASE_URL=settings.DATABASE_URL,
        SECRET_KEY=settings.SECRET_KEY,
    )
    assert prod_settings.SESSION_COOKIE_SECURE is True

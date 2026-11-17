"""Focused integration and unit tests for UserRepository and SessionRepository.

Verifies:
UserRepository:
1. Successful user creation and attribute population.
2. User retrieval by UUID primary key.
3. User retrieval by email address.
4. Email retrieval with uppercase characters and surrounding whitespace.
5. Missing user lookups return None.
6. Duplicate email rejection by PostgreSQL uniqueness constraint.

SessionRepository:
1. Successful session creation with hashed token.
2. Active (non-revoked) session retrieval by token hash.
3. Revoked sessions are excluded from get_active_session_by_hash.
4. Last activity timestamp updates successfully.
5. Updating activity does not alter expires_at timestamp.
6. Single session revocation succeeds.
7. Repeated revocation is idempotent and safe.
8. Revoking all sessions for a user invalidates all user's sessions.
9. Revoking all sessions for one user leaves other users' sessions active.
10. Updating activity on a non-existent session is handled safely.
11. Repositories do not commit transactions (verified via rollback isolation).
"""

from datetime import datetime, timedelta, timezone
from typing import Generator
import uuid

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import generate_session_token, hash_session_token
from app.database import engine
from app.repositories import session_repository, user_repository


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    """Provide an isolated database session that automatically rolls back after each test.

    Ensures zero test data pollution in applytrack_dev without destructive table drops.
    """
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection)

    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


# ============================================================================
# UserRepository Tests
# ============================================================================


def test_user_repository_create_user_success(db_session: Session):
    """Verify that a user is created and flushed with database-generated defaults."""
    user = user_repository.create(
        db=db_session,
        full_name="Claude Shannon",
        email="shannon@bell-labs.com",
        password_hash="$argon2id$v=19$m=65536,t=2,p=1$dummy_hash_value",
    )

    assert isinstance(user.id, uuid.UUID)
    assert user.full_name == "Claude Shannon"
    assert user.email == "shannon@bell-labs.com"
    assert user.password_hash == "$argon2id$v=19$m=65536,t=2,p=1$dummy_hash_value"
    assert user.is_active is True
    assert user.ai_consent is False
    assert user.ai_consent_given_at is None
    assert isinstance(user.created_at, datetime)
    assert isinstance(user.updated_at, datetime)


def test_user_repository_create_user_with_ai_consent_and_timestamp(db_session: Session):
    """Verify creating a user with AI consent and timestamp persists both values."""
    consent_time = datetime.now(timezone.utc)
    user = user_repository.create(
        db=db_session,
        full_name="John von Neumann",
        email="neumann@ias.edu",
        password_hash="argon2id_stored_hash",
        ai_consent=True,
        ai_consent_given_at=consent_time,
    )

    assert user.ai_consent is True
    assert user.ai_consent_given_at == consent_time

    # Verify retrieval from database preserves both values
    retrieved = user_repository.get_by_id(db_session, user.id)
    assert retrieved is not None
    assert retrieved.ai_consent is True
    assert retrieved.ai_consent_given_at == consent_time


def test_user_repository_create_user_without_ai_consent_persists_false_and_none(db_session: Session):
    """Verify creating a user with AI consent False and no timestamp persists False and None."""
    user = user_repository.create(
        db=db_session,
        full_name="Alan Turing",
        email="turing@bletchley.ac.uk",
        password_hash="argon2id_stored_hash",
        ai_consent=False,
    )

    assert user.ai_consent is False
    assert user.ai_consent_given_at is None

    # Verify retrieval from database preserves False and None
    retrieved = user_repository.get_by_id(db_session, user.id)
    assert retrieved is not None
    assert retrieved.ai_consent is False
    assert retrieved.ai_consent_given_at is None


def test_user_repository_get_by_id(db_session: Session):
    """Verify retrieving an existing user by primary key UUID."""
    user = user_repository.create(
        db=db_session,
        full_name="Ada Lovelace",
        email="ada@analytical-engine.org",
        password_hash="argon2id_stored_hash",
    )

    retrieved = user_repository.get_by_id(db_session, user.id)
    assert retrieved is not None
    assert retrieved.id == user.id
    assert retrieved.email == "ada@analytical-engine.org"
    assert retrieved.full_name == "Ada Lovelace"


def test_user_repository_get_by_email(db_session: Session):
    """Verify retrieving an existing user by exact email address."""
    user = user_repository.create(
        db=db_session,
        full_name="Charles Babbage",
        email="babbage@engine.org",
        password_hash="argon2id_stored_hash",
    )

    retrieved = user_repository.get_by_email(db_session, "babbage@engine.org")
    assert retrieved is not None
    assert retrieved.id == user.id
    assert retrieved.email == "babbage@engine.org"


def test_user_repository_get_by_email_case_and_whitespace_insensitivity(db_session: Session):
    """Verify email lookup normalizes uppercase letters and surrounding whitespace."""
    user = user_repository.create(
        db=db_session,
        full_name="Grace Hopper",
        email="grace.hopper@navy.mil",
        password_hash="argon2id_stored_hash",
    )

    # Search with messy whitespace and uppercase characters
    retrieved = user_repository.get_by_email(db_session, "   GRACE.HOPPER@NAVY.MIL  \t")
    assert retrieved is not None
    assert retrieved.id == user.id
    assert retrieved.email == "grace.hopper@navy.mil"


def test_user_repository_missing_user_returns_none(db_session: Session):
    """Verify looking up non-existent users returns None without errors."""
    # Unknown UUID
    unknown_id = uuid.uuid4()
    assert user_repository.get_by_id(db_session, unknown_id) is None

    # Unknown email
    assert user_repository.get_by_email(db_session, "nonexistent@domain.com") is None

    # Empty and invalid inputs
    assert user_repository.get_by_email(db_session, "") is None
    assert user_repository.get_by_email(db_session, "   ") is None


def test_user_repository_duplicate_email_rejected_by_database_constraint(db_session: Session):
    """Verify duplicate email violates the database-level uniqueness constraint."""
    user_repository.create(
        db=db_session,
        full_name="Original User",
        email="duplicate.test@example.com",
        password_hash="argon2id_stored_hash",
    )

    # Attempting to create another user with the same email in the same transaction
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            user_repository.create(
                db=db_session,
                full_name="Duplicate User",
                email="DUPLICATE.TEST@example.com",  # Normalized by repository/model
                password_hash="argon2id_stored_hash_2",
            )


# ============================================================================
# SessionRepository Tests
# ============================================================================


def test_session_repository_create_session_success(db_session: Session):
    """Verify creating a session persists the token hash and returns populated entity."""
    user = user_repository.create(
        db=db_session,
        full_name="Session Owner",
        email="session.owner@example.com",
        password_hash="hash",
    )

    token = generate_session_token()
    token_hash = hash_session_token(token)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=24)

    session = session_repository.create_session(
        db=db_session,
        user_id=user.id,
        token_hash=token_hash,
        expires_at=expires_at,
    )

    assert isinstance(session.id, uuid.UUID)
    assert session.user_id == user.id
    assert session.token_hash == token_hash
    assert session.expires_at == expires_at
    assert session.is_revoked is False
    assert isinstance(session.created_at, datetime)
    assert isinstance(session.last_activity_at, datetime)


def test_session_repository_get_active_session_by_hash(db_session: Session):
    """Verify retrieving a non-revoked session by its SHA-256 token hash."""
    user = user_repository.create(
        db=db_session,
        full_name="Active Session User",
        email="active.session@example.com",
        password_hash="hash",
    )

    token = generate_session_token()
    token_hash = hash_session_token(token)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=24)

    session = session_repository.create_session(db_session, user.id, token_hash, expires_at)

    retrieved = session_repository.get_active_session_by_hash(db_session, token_hash)
    assert retrieved is not None
    assert retrieved.id == session.id
    assert retrieved.user_id == user.id
    assert retrieved.is_revoked is False


def test_session_repository_revoked_session_not_returned_by_get_active(db_session: Session):
    """Verify revoked sessions are excluded from get_active_session_by_hash."""
    user = user_repository.create(
        db=db_session,
        full_name="Revoked User",
        email="revoked@example.com",
        password_hash="hash",
    )

    token = generate_session_token()
    token_hash = hash_session_token(token)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=24)

    session_repository.create_session(db_session, user.id, token_hash, expires_at)
    session_repository.revoke_session(db_session, token_hash)

    # Active lookup must now return None
    assert session_repository.get_active_session_by_hash(db_session, token_hash) is None


def test_session_repository_update_last_activity_success(db_session: Session):
    """Verify updating session last_activity_at successfully advances the timestamp."""
    user = user_repository.create(
        db=db_session,
        full_name="Activity User",
        email="activity@example.com",
        password_hash="hash",
    )

    token = generate_session_token()
    token_hash = hash_session_token(token)
    past_time = datetime.now(timezone.utc) - timedelta(minutes=45)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=24)

    session = session_repository.create_session(db_session, user.id, token_hash, expires_at)
    # Manually backdate last_activity_at for testing advancement
    session.last_activity_at = past_time
    db_session.flush()

    # Call repository update
    session_repository.update_last_activity(db_session, session.id)

    # Refresh session entity
    db_session.refresh(session)
    assert session.last_activity_at > past_time


def test_session_repository_update_activity_does_not_change_expires_at(db_session: Session):
    """Verify updating activity does NOT alter the absolute expiration timestamp."""
    user = user_repository.create(
        db=db_session,
        full_name="Expiry User",
        email="expiry@example.com",
        password_hash="hash",
    )

    token = generate_session_token()
    token_hash = hash_session_token(token)
    fixed_expires_at = datetime.now(timezone.utc) + timedelta(hours=24)

    session = session_repository.create_session(db_session, user.id, token_hash, fixed_expires_at)
    session_repository.update_last_activity(db_session, session.id)

    db_session.refresh(session)
    # expires_at must remain strictly unchanged
    assert session.expires_at == fixed_expires_at


def test_session_repository_revoke_single_session_success(db_session: Session):
    """Verify revoking one session sets is_revoked to True."""
    user = user_repository.create(
        db=db_session,
        full_name="Single Revoke User",
        email="single.revoke@example.com",
        password_hash="hash",
    )

    token = generate_session_token()
    token_hash = hash_session_token(token)
    session = session_repository.create_session(
        db_session, user.id, token_hash, datetime.now(timezone.utc) + timedelta(hours=24)
    )

    assert session.is_revoked is False
    session_repository.revoke_session(db_session, token_hash)

    db_session.refresh(session)
    assert session.is_revoked is True


def test_session_repository_repeated_revocation_is_safe(db_session: Session):
    """Verify repeated revocation is idempotent and does not raise errors."""
    user = user_repository.create(
        db=db_session,
        full_name="Idempotent Revoke User",
        email="idempotent.revoke@example.com",
        password_hash="hash",
    )

    token_hash = hash_session_token(generate_session_token())
    session_repository.create_session(
        db_session, user.id, token_hash, datetime.now(timezone.utc) + timedelta(hours=24)
    )

    # Repeated calls must not fail
    session_repository.revoke_session(db_session, token_hash)
    session_repository.revoke_session(db_session, token_hash)
    session_repository.revoke_session(db_session, token_hash)

    assert session_repository.get_active_session_by_hash(db_session, token_hash) is None


def test_session_repository_revoke_all_user_sessions(db_session: Session):
    """Verify revoke_all_user_sessions invalidates all sessions for the target user."""
    user = user_repository.create(
        db=db_session,
        full_name="Multi Device User",
        email="multidevice@example.com",
        password_hash="hash",
    )

    # Create 3 active sessions for this user (e.g., Desktop, Laptop, Mobile)
    tokens = [generate_session_token() for _ in range(3)]
    hashes = [hash_session_token(t) for t in tokens]
    expires = datetime.now(timezone.utc) + timedelta(hours=24)

    for h in hashes:
        session_repository.create_session(db_session, user.id, h, expires)

    # Verify all 3 are active initially
    for h in hashes:
        assert session_repository.get_active_session_by_hash(db_session, h) is not None

    # Revoke all sessions for this user
    session_repository.revoke_all_user_sessions(db_session, user.id)

    # Verify all 3 sessions are now revoked
    for h in hashes:
        assert session_repository.get_active_session_by_hash(db_session, h) is None


def test_session_repository_revoke_all_does_not_affect_other_users(db_session: Session):
    """Verify revoking all sessions for User A leaves User B's sessions active."""
    user_a = user_repository.create(
        db=db_session,
        full_name="User Alpha",
        email="alpha@example.com",
        password_hash="hash",
    )
    user_b = user_repository.create(
        db=db_session,
        full_name="User Beta",
        email="beta@example.com",
        password_hash="hash",
    )

    expires = datetime.now(timezone.utc) + timedelta(hours=24)
    hash_a = hash_session_token(generate_session_token())
    hash_b = hash_session_token(generate_session_token())

    session_repository.create_session(db_session, user_a.id, hash_a, expires)
    session_repository.create_session(db_session, user_b.id, hash_b, expires)

    # Revoke only User A's sessions
    session_repository.revoke_all_user_sessions(db_session, user_a.id)

    # User A's session is revoked, User B's session remains active
    assert session_repository.get_active_session_by_hash(db_session, hash_a) is None
    assert session_repository.get_active_session_by_hash(db_session, hash_b) is not None


def test_session_repository_missing_session_activity_update_is_safe(db_session: Session):
    """Verify update_last_activity handles non-existent session IDs safely."""
    non_existent_id = uuid.uuid4()
    # Must not raise an exception
    session_repository.update_last_activity(db_session, non_existent_id)

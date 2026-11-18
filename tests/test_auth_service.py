"""Comprehensive unit and integration tests for AuthService and authentication business logic.

Verifies:
1. Registration:
   - Successful user registration with UserCreate Pydantic schema.
   - Successful user registration with individual keyword arguments.
   - Password is stored as an Argon2id hash (never plaintext).
   - Duplicate email registration is prevented with DuplicateEmailError (case-insensitive).
   - AI consent flag True sets UTC timestamp ai_consent_given_at.
   - AI consent flag False stores False and None timestamp.
   - Transaction rollback on unexpected failure.
   - Rejection of whitespace-only full names.
   - Rejection of whitespace-only emails.
   - Keyword-argument registration rejects malformed email addresses.
   - Simulated concurrent duplicate email IntegrityError yields DuplicateEmailError.
   - Unrelated IntegrityError is re-raised after transaction rollback.

2. Authentication:
   - Successful authentication with valid email and password.
   - Incorrect password raises InvalidCredentialsError.
   - Non-existent email raises InvalidCredentialsError and executes dummy_verify_password.
   - Inactive user (is_active=False) raises InvalidCredentialsError.
   - Uniform error message "Invalid email or password." across all failure cases to prevent enumeration.

3. Session Creation:
   - Returns a high-entropy 256-bit raw session token.
   - Persists only the SHA-256 token hash in the database.
   - Sets absolute expiration to 24 hours in the future.
   - Initializes last_activity_at timestamp.

4. Session Validation:
   - Valid session returns authenticated User and updates last_activity_at.
   - Unknown raw token raises InvalidSessionError.
   - Already revoked session raises InvalidSessionError.
   - Absolute expiration (>24h) revokes session and raises InvalidSessionError.
   - Idle timeout (>120m inactive) revokes session and raises InvalidSessionError.
   - Inactive user account revokes session and raises InvalidSessionError.
   - Activity updates advance last_activity_at without extending expires_at.
   - Failed session activity commit triggers transaction rollback.
   - Failed session revocation commit triggers transaction rollback.
   - Successful expiration revocation still commits before InvalidSessionError is raised.

5. Logout and Revocation:
   - logout(raw_token) revokes the session in the database.
   - logout with invalid or missing token is safe and idempotent.
   - revoke_all_sessions(user_id) revokes all sessions belonging to the user.
   - revoke_all_sessions isolates revocation and leaves other users' sessions active.
"""

from datetime import datetime, timedelta, timezone
from typing import Generator
from unittest.mock import patch
import uuid

import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.core.security import hash_session_token, verify_password
from app.database import engine
from app.models.user import User
from app.models.user_session import UserSession
from app.schemas.user import UserCreate
from app.services.auth_service import (
    AuthService,
    DuplicateEmailError,
    InvalidCredentialsError,
    InvalidSessionError,
)


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    """Provide an isolated database session that automatically rolls back after each test.

    Uses join_transaction_mode='create_savepoint' so that AuthService.commit()
    operates against savepoints and the outer transaction can be cleanly rolled back.
    Ensures zero database pollution in applytrack_dev.
    """
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")

    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture
def auth_service(db_session: Session) -> AuthService:
    """Provide an AuthService instance wired to the transactional test session."""
    return AuthService(db=db_session)


# ============================================================================
# 1. Registration Tests
# ============================================================================


def test_register_user_success_with_schema(auth_service: AuthService):
    """Verify successful user registration using a UserCreate Pydantic schema."""
    user_in = UserCreate(
        full_name="Grace Hopper",
        email="grace.hopper@navy.mil",
        password="SecurePassword123!",
        ai_consent=True,
    )

    user = auth_service.register(user_in)

    assert isinstance(user.id, uuid.UUID)
    assert user.full_name == "Grace Hopper"
    assert user.email == "grace.hopper@navy.mil"
    assert user.is_active is True
    assert user.ai_consent is True
    assert isinstance(user.ai_consent_given_at, datetime)
    assert user.ai_consent_given_at.tzinfo is not None


def test_register_user_success_with_kwargs(auth_service: AuthService):
    """Verify successful user registration using keyword arguments."""
    user = auth_service.register(
        full_name="Ada Lovelace",
        email="ada@analytical-engine.org",
        password="FirstProgrammer1843!",
        ai_consent=False,
    )

    assert isinstance(user.id, uuid.UUID)
    assert user.full_name == "Ada Lovelace"
    assert user.email == "ada@analytical-engine.org"
    assert user.is_active is True
    assert user.ai_consent is False
    assert user.ai_consent_given_at is None


def test_register_stores_argon2id_hash_never_plaintext(auth_service: AuthService):
    """Verify password is stored strictly as an Argon2id hash and never plaintext."""
    plaintext = "SuperSecretPassword123!"
    user = auth_service.register(
        full_name="Alan Turing",
        email="turing@bletchley.ac.uk",
        password=plaintext,
    )

    assert user.password_hash != plaintext
    assert user.password_hash.startswith("$argon2id$")
    assert verify_password(plaintext, user.password_hash) is True


def test_register_duplicate_email_raises_duplicate_email_error(auth_service: AuthService):
    """Verify attempting to register with an existing email raises DuplicateEmailError."""
    auth_service.register(
        full_name="Original User",
        email="duplicate@example.com",
        password="Password123!",
    )

    # Exact match
    with pytest.raises(DuplicateEmailError, match="already exists"):
        auth_service.register(
            full_name="Duplicate User",
            email="duplicate@example.com",
            password="OtherPassword123!",
        )

    # Case-insensitive match with surrounding whitespace
    with pytest.raises(DuplicateEmailError, match="already exists"):
        auth_service.register(
            full_name="Duplicate User Upper",
            email="  DUPLICATE@EXAMPLE.COM  ",
            password="OtherPassword123!",
        )


def test_register_duplicate_email_calls_dummy_verify_password(auth_service: AuthService):
    """Verify attempting to register with an existing email performs dummy Argon2 verification for timing consistency."""
    auth_service.register(
        full_name="Existing User",
        email="timing.duplicate@example.com",
        password="Password123!",
    )

    with patch("app.services.auth_service.dummy_verify_password", wraps=None) as mock_dummy:
        with pytest.raises(DuplicateEmailError, match="already exists"):
            auth_service.register(
                full_name="Duplicate User",
                email="timing.duplicate@example.com",
                password="OtherPassword123!",
            )
        mock_dummy.assert_called_once_with("OtherPassword123!")


def test_register_missing_fields_raises_value_error(auth_service: AuthService):
    """Verify calling register with missing mandatory fields raises ValueError."""
    with pytest.raises(ValueError):
        auth_service.register(full_name="", email="test@example.com", password="Password123!")

    with pytest.raises(ValueError):
        auth_service.register(full_name="Test User", email="", password="Password123!")

    with pytest.raises(ValueError):
        auth_service.register(full_name="Test User", email="test@example.com", password="")


def test_register_whitespace_only_full_name_rejected(auth_service: AuthService):
    """Verify registration rejects whitespace-only full names in both kwargs and schema."""
    with pytest.raises(ValueError, match="whitespace"):
        auth_service.register(
            full_name="   \t\n  ",
            email="valid.name@example.com",
            password="Password123!",
        )

    # Schema direct validation path
    with pytest.raises(ValueError, match="whitespace"):
        UserCreate(
            full_name="     ",
            email="valid.name@example.com",
            password="Password123!",
        )


def test_register_whitespace_only_email_rejected(auth_service: AuthService):
    """Verify registration rejects whitespace-only emails."""
    with pytest.raises(ValueError):
        auth_service.register(
            full_name="Valid Full Name",
            email="   \t\n  ",
            password="Password123!",
        )


def test_register_kwargs_malformed_email_rejected(auth_service: AuthService):
    """Verify keyword-argument registration validates and rejects malformed email formats."""
    malformed_emails = [
        "notanemail",
        "missingatsign.com",
        "user@.com",
        "@domain.com",
        "user@domain..com",
    ]
    for bad_email in malformed_emails:
        with pytest.raises(ValueError):
            auth_service.register(
                full_name="Valid User",
                email=bad_email,
                password="Password123!",
            )


def test_register_concurrent_duplicate_email_becomes_duplicate_email_error(
    auth_service: AuthService,
):
    """Verify concurrent registration race condition raising IntegrityError yields DuplicateEmailError."""
    fake_user = User(
        full_name="Existing User",
        email="concurrent@example.com",
        password_hash="some_hash",
    )
    # 1st lookup (preliminary) returns None; 2nd lookup (post-IntegrityError) returns fake_user
    with patch(
        "app.repositories.user_repository.get_by_email",
        side_effect=[None, fake_user],
    ):
        with patch(
            "app.repositories.user_repository.create",
            side_effect=IntegrityError("duplicate key", params={}, orig=Exception()),
        ):
            with pytest.raises(DuplicateEmailError, match="already exists"):
                auth_service.register(
                    full_name="Concurrent Candidate",
                    email="concurrent@example.com",
                    password="Password123!",
                )


def test_register_unrelated_integrity_error_re_raised(auth_service: AuthService):
    """Verify an IntegrityError unrelated to duplicate email is re-raised after transaction rollback."""
    # Lookup returns None on both preliminary and post-rollback lookups
    with patch(
        "app.repositories.user_repository.get_by_email",
        return_value=None,
    ):
        with patch(
            "app.repositories.user_repository.create",
            side_effect=IntegrityError("foreign key or check constraint failure", params={}, orig=Exception()),
        ):
            with pytest.raises(IntegrityError, match="foreign key or check constraint failure"):
                auth_service.register(
                    full_name="Unrelated Error Candidate",
                    email="unrelated@example.com",
                    password="Password123!",
                )


def test_register_rollback_on_failure(auth_service: AuthService, db_session: Session):
    """Verify database transaction rolls back if an unhandled error occurs during creation."""
    with patch("app.repositories.user_repository.create", side_effect=RuntimeError("Database failure")):
        with pytest.raises(RuntimeError, match="Database failure"):
            auth_service.register(
                full_name="Failure Case",
                email="fail@example.com",
                password="Password123!",
            )

    # Verify no row was inserted
    stmt = select(User).where(User.email == "fail@example.com")
    assert db_session.scalar(stmt) is None


# ============================================================================
# 2. Authentication Tests
# ============================================================================


def test_authenticate_success(auth_service: AuthService):
    """Verify user can authenticate with correct email and password."""
    registered = auth_service.register(
        full_name="Katherine Johnson",
        email="kjohnson@nasa.gov",
        password="OrbitalMechanics1969!",
    )

    authenticated = auth_service.authenticate("kjohnson@nasa.gov", "OrbitalMechanics1969!")
    assert authenticated.id == registered.id
    assert authenticated.email == "kjohnson@nasa.gov"

    # Authenticate with different casing and whitespace
    authenticated_case = auth_service.authenticate("  KJOHNSON@NASA.GOV  ", "OrbitalMechanics1969!")
    assert authenticated_case.id == registered.id


def test_authenticate_incorrect_password(auth_service: AuthService):
    """Verify incorrect password raises InvalidCredentialsError."""
    auth_service.register(
        full_name="Margaret Hamilton",
        email="mhamilton@mit.edu",
        password="ApolloSoftware1969!",
    )

    with pytest.raises(InvalidCredentialsError, match="Invalid email or password."):
        auth_service.authenticate("mhamilton@mit.edu", "WrongPassword!")


def test_authenticate_nonexistent_email_calls_dummy_verify(auth_service: AuthService):
    """Verify non-existent email raises InvalidCredentialsError and executes dummy verification."""
    with patch("app.services.auth_service.dummy_verify_password", wraps=None) as mock_dummy:
        with pytest.raises(InvalidCredentialsError, match="Invalid email or password."):
            auth_service.authenticate("nonexistent@example.com", "SomePassword123!")

        mock_dummy.assert_called_once_with("SomePassword123!")


def test_authenticate_inactive_user(auth_service: AuthService, db_session: Session):
    """Verify inactive user raises InvalidCredentialsError with generic error message."""
    user = auth_service.register(
        full_name="Inactive User",
        email="inactive@example.com",
        password="Password123!",
    )

    # Deactivate user
    db_session.execute(
        update(User).where(User.id == user.id).values(is_active=False)
    )
    db_session.commit()

    with pytest.raises(InvalidCredentialsError, match="Invalid email or password."):
        auth_service.authenticate("inactive@example.com", "Password123!")


def test_authenticate_identical_error_messages_prevent_enumeration(
    auth_service: AuthService, db_session: Session
):
    """Verify all failure scenarios return the identical message to prevent user enumeration."""
    auth_service.register(
        full_name="Active User",
        email="active@example.com",
        password="CorrectPassword123!",
    )
    inactive_user = auth_service.register(
        full_name="Inactive User 2",
        email="inactive2@example.com",
        password="CorrectPassword123!",
    )
    db_session.execute(
        update(User).where(User.id == inactive_user.id).values(is_active=False)
    )
    db_session.commit()

    errors = []

    # 1. Nonexistent email
    try:
        auth_service.authenticate("unknown@example.com", "AnyPassword123!")
    except InvalidCredentialsError as exc:
        errors.append(str(exc))

    # 2. Wrong password
    try:
        auth_service.authenticate("active@example.com", "WrongPassword123!")
    except InvalidCredentialsError as exc:
        errors.append(str(exc))

    # 3. Inactive account
    try:
        auth_service.authenticate("inactive2@example.com", "CorrectPassword123!")
    except InvalidCredentialsError as exc:
        errors.append(str(exc))

    # 4. Empty email / password
    try:
        auth_service.authenticate("", "SomePassword123!")
    except InvalidCredentialsError as exc:
        errors.append(str(exc))

    assert len(errors) == 4
    for error_msg in errors:
        assert error_msg == "Invalid email or password."


# ============================================================================
# 3. Session Creation Tests
# ============================================================================


def test_create_session_success(auth_service: AuthService, db_session: Session):
    """Verify creating a session returns raw token and persists hash with 24h expiration."""
    user = auth_service.register(
        full_name="Dennis Ritchie",
        email="dmr@bell-labs.com",
        password="CProgramming1972!",
    )

    now_before = datetime.now(timezone.utc)
    raw_token = auth_service.create_session(user.id)
    now_after = datetime.now(timezone.utc)

    # Raw token verification: 32 bytes URL-safe base64 string (43 characters)
    assert isinstance(raw_token, str)
    assert len(raw_token) >= 43

    # Verify database persistence: token_hash matches SHA-256 of raw_token
    token_hash = hash_session_token(raw_token)
    stmt = select(UserSession).where(UserSession.token_hash == token_hash)
    session_record = db_session.scalar(stmt)

    assert session_record is not None
    assert session_record.user_id == user.id
    assert session_record.is_revoked is False

    # Raw token is NOT stored in the database
    assert session_record.token_hash != raw_token

    # Verify absolute expiration (~24 hours from creation)
    expected_expiry = now_before + timedelta(hours=settings.SESSION_ABSOLUTE_LIFETIME_HOURS)
    assert abs((session_record.expires_at - expected_expiry).total_seconds()) < 10

    # Verify last_activity_at initialized to now
    assert session_record.last_activity_at >= now_before - timedelta(seconds=1)
    assert session_record.last_activity_at <= now_after + timedelta(seconds=1)


# ============================================================================
# 4. Session Validation Tests
# ============================================================================


def test_validate_session_success(auth_service: AuthService, db_session: Session):
    """Verify valid raw session token returns User and advances last_activity_at."""
    user = auth_service.register(
        full_name="Ken Thompson",
        email="ken@bell-labs.com",
        password="UnixPhilosophy1970!",
    )

    raw_token = auth_service.create_session(user.id)
    token_hash = hash_session_token(raw_token)

    # Set last_activity_at to 10 minutes ago
    ten_mins_ago = datetime.now(timezone.utc) - timedelta(minutes=10)
    db_session.execute(
        update(UserSession)
        .where(UserSession.token_hash == token_hash)
        .values(last_activity_at=ten_mins_ago)
    )
    db_session.commit()

    validated_user = auth_service.validate_session(raw_token)
    assert validated_user.id == user.id
    assert validated_user.email == "ken@bell-labs.com"

    # Verify last_activity_at was updated to the current time
    stmt = select(UserSession).where(UserSession.token_hash == token_hash)
    updated_session = db_session.scalar(stmt)
    assert updated_session.last_activity_at > ten_mins_ago


def test_validate_session_unknown_token(auth_service: AuthService):
    """Verify unknown or malformed raw token raises InvalidSessionError."""
    # Malformed / empty tokens
    with pytest.raises(InvalidSessionError, match="Invalid or expired session."):
        auth_service.validate_session("")

    # Random valid-format token that does not exist in DB
    fake_token = "f" * 64
    with pytest.raises(InvalidSessionError, match="Invalid or expired session."):
        auth_service.validate_session(fake_token)


def test_validate_session_revoked(auth_service: AuthService, db_session: Session):
    """Verify already revoked session raises InvalidSessionError."""
    user = auth_service.register(
        full_name="Donald Knuth",
        email="knuth@stanford.edu",
        password="TheArtOfComputer1968!",
    )
    raw_token = auth_service.create_session(user.id)
    token_hash = hash_session_token(raw_token)

    # Mark as revoked
    db_session.execute(
        update(UserSession)
        .where(UserSession.token_hash == token_hash)
        .values(is_revoked=True)
    )
    db_session.commit()

    with pytest.raises(InvalidSessionError, match="Invalid or expired session."):
        auth_service.validate_session(raw_token)


def test_validate_session_absolute_expiration(auth_service: AuthService, db_session: Session):
    """Verify session exceeding absolute 24-hour lifetime is revoked and raises error."""
    user = auth_service.register(
        full_name="Barbara Liskov",
        email="liskov@mit.edu",
        password="SubstitutionPrinciple1987!",
    )
    raw_token = auth_service.create_session(user.id)
    token_hash = hash_session_token(raw_token)

    # Set expires_at to 1 minute in the past
    past_expiry = datetime.now(timezone.utc) - timedelta(minutes=1)
    db_session.execute(
        update(UserSession)
        .where(UserSession.token_hash == token_hash)
        .values(expires_at=past_expiry)
    )
    db_session.commit()

    with pytest.raises(InvalidSessionError, match="Session has expired."):
        auth_service.validate_session(raw_token)

    # Verify session was marked revoked in DB
    stmt = select(UserSession).where(UserSession.token_hash == token_hash)
    revoked_session = db_session.scalar(stmt)
    assert revoked_session.is_revoked is True


def test_validate_session_idle_timeout(auth_service: AuthService, db_session: Session):
    """Verify session exceeding idle timeout (120 minutes) is revoked and raises error."""
    user = auth_service.register(
        full_name="Edsger Dijkstra",
        email="dijkstra@utexas.edu",
        password="ShortestPath1956!",
    )
    raw_token = auth_service.create_session(user.id)
    token_hash = hash_session_token(raw_token)

    # Set last_activity_at to 121 minutes ago (idle timeout is 120 minutes)
    idle_time = datetime.now(timezone.utc) - timedelta(
        minutes=settings.SESSION_IDLE_TIMEOUT_MINUTES + 1
    )
    db_session.execute(
        update(UserSession)
        .where(UserSession.token_hash == token_hash)
        .values(last_activity_at=idle_time)
    )
    db_session.commit()

    with pytest.raises(InvalidSessionError, match="Session expired due to inactivity."):
        auth_service.validate_session(raw_token)

    # Verify session was marked revoked in DB
    stmt = select(UserSession).where(UserSession.token_hash == token_hash)
    revoked_session = db_session.scalar(stmt)
    assert revoked_session.is_revoked is True


def test_validate_session_inactive_user(auth_service: AuthService, db_session: Session):
    """Verify session for deactivated user account is revoked and raises error."""
    user = auth_service.register(
        full_name="Leslie Lamport",
        email="lamport@microsoft.com",
        password="PaxosConsensus1998!",
    )
    raw_token = auth_service.create_session(user.id)
    token_hash = hash_session_token(raw_token)

    # Deactivate user
    db_session.execute(
        update(User).where(User.id == user.id).values(is_active=False)
    )
    db_session.commit()

    with pytest.raises(InvalidSessionError, match="User account is inactive or missing."):
        auth_service.validate_session(raw_token)

    # Verify session was marked revoked in DB
    stmt = select(UserSession).where(UserSession.token_hash == token_hash)
    revoked_session = db_session.scalar(stmt)
    assert revoked_session.is_revoked is True


def test_validate_session_activity_update_preserves_expires_at(
    auth_service: AuthService, db_session: Session
):
    """Verify validate_session advances last_activity_at without modifying expires_at."""
    user = auth_service.register(
        full_name="Linus Torvalds",
        email="torvalds@kernel.org",
        password="GitAndLinux1991!",
    )
    raw_token = auth_service.create_session(user.id)
    token_hash = hash_session_token(raw_token)

    stmt = select(UserSession).where(UserSession.token_hash == token_hash)
    initial_session = db_session.scalar(stmt)
    original_expires_at = initial_session.expires_at

    # Wait or simulate validation
    auth_service.validate_session(raw_token)

    db_session.expire_all()
    updated_session = db_session.scalar(stmt)

    assert updated_session.expires_at == original_expires_at


def test_validate_session_failed_activity_commit_triggers_rollback(
    auth_service: AuthService, db_session: Session
):
    """Verify database commit failure during session activity update triggers rollback."""
    user = auth_service.register(
        full_name="Commit Fail User",
        email="commit.fail@example.com",
        password="Password123!",
    )
    raw_token = auth_service.create_session(user.id)

    # Force commit to fail when updating last_activity_at in validate_session
    with patch.object(auth_service.db, "commit", side_effect=RuntimeError("Database commit failed")):
        with patch.object(auth_service.db, "rollback", wraps=auth_service.db.rollback) as mock_rollback:
            with pytest.raises(RuntimeError, match="Database commit failed"):
                auth_service.validate_session(raw_token)
            mock_rollback.assert_called_once()


def test_validate_session_failed_revocation_commit_triggers_rollback(
    auth_service: AuthService, db_session: Session
):
    """Verify database commit failure during session revocation triggers rollback."""
    user = auth_service.register(
        full_name="Revoke Fail User",
        email="revoke.fail@example.com",
        password="Password123!",
    )
    raw_token = auth_service.create_session(user.id)
    token_hash = hash_session_token(raw_token)

    # Expire session in database
    past_expiry = datetime.now(timezone.utc) - timedelta(minutes=5)
    db_session.execute(
        update(UserSession)
        .where(UserSession.token_hash == token_hash)
        .values(expires_at=past_expiry)
    )
    db_session.commit()

    # Force commit to fail when committing revocation in _revoke_and_raise
    with patch.object(auth_service.db, "commit", side_effect=RuntimeError("Revocation commit failed")):
        with patch.object(auth_service.db, "rollback", wraps=auth_service.db.rollback) as mock_rollback:
            with pytest.raises(RuntimeError, match="Revocation commit failed"):
                auth_service.validate_session(raw_token)
            mock_rollback.assert_called_once()


def test_validate_session_successful_expiration_revocation_commits_before_raising(
    auth_service: AuthService, db_session: Session
):
    """Verify expired session revocation is committed in database before InvalidSessionError is raised."""
    user = auth_service.register(
        full_name="Expiration Commit User",
        email="expiration.commit@example.com",
        password="Password123!",
    )
    raw_token = auth_service.create_session(user.id)
    token_hash = hash_session_token(raw_token)

    # Expire session in database
    past_expiry = datetime.now(timezone.utc) - timedelta(minutes=5)
    db_session.execute(
        update(UserSession)
        .where(UserSession.token_hash == token_hash)
        .values(expires_at=past_expiry)
    )
    db_session.commit()

    # Validation must raise InvalidSessionError
    with pytest.raises(InvalidSessionError, match="Session has expired."):
        auth_service.validate_session(raw_token)

    # Verify that in database, the revocation WAS committed (is_revoked is True)
    # and was NOT accidentally rolled back when InvalidSessionError was raised
    stmt = select(UserSession).where(UserSession.token_hash == token_hash)
    revoked_session = db_session.scalar(stmt)
    assert revoked_session is not None
    assert revoked_session.is_revoked is True


def test_validate_session_get_active_session_failure_triggers_rollback(
    auth_service: AuthService,
):
    """Verify database lookup failure in get_active_session_by_hash triggers rollback."""
    with patch(
        "app.repositories.session_repository.get_active_session_by_hash",
        side_effect=RuntimeError("Database query failure"),
    ):
        with patch.object(auth_service.db, "rollback", wraps=auth_service.db.rollback) as mock_rollback:
            with pytest.raises(RuntimeError, match="Database query failure"):
                auth_service.validate_session("some_valid_looking_raw_token_string_123456789012345")
            mock_rollback.assert_called_once()


def test_validate_session_get_user_by_id_failure_triggers_rollback(
    auth_service: AuthService,
):
    """Verify database lookup failure in user_repository.get_by_id triggers rollback."""
    user = auth_service.register(
        full_name="Lookup Fail User",
        email="lookup.fail@example.com",
        password="Password123!",
    )
    raw_token = auth_service.create_session(user.id)

    with patch(
        "app.repositories.user_repository.get_by_id",
        side_effect=RuntimeError("User query failure"),
    ):
        with patch.object(auth_service.db, "rollback", wraps=auth_service.db.rollback) as mock_rollback:
            with pytest.raises(RuntimeError, match="User query failure"):
                auth_service.validate_session(raw_token)
            mock_rollback.assert_called_once()


# ============================================================================
# 5. Logout and Revocation Tests
# ============================================================================


def test_logout_revokes_session(auth_service: AuthService, db_session: Session):
    """Verify logout marks the session as revoked in the database."""
    user = auth_service.register(
        full_name="Bjarne Stroustrup",
        email="bjarne@stroustrup.com",
        password="CppProgramming1985!",
    )
    raw_token = auth_service.create_session(user.id)
    token_hash = hash_session_token(raw_token)

    # Perform logout
    auth_service.logout(raw_token)

    # Verify session is revoked
    stmt = select(UserSession).where(UserSession.token_hash == token_hash)
    session_record = db_session.scalar(stmt)
    assert session_record.is_revoked is True

    # Validate session must fail
    with pytest.raises(InvalidSessionError, match="Invalid or expired session."):
        auth_service.validate_session(raw_token)


def test_logout_invalid_or_missing_token_is_safe(auth_service: AuthService):
    """Verify calling logout with missing or non-existent token does not raise."""
    auth_service.logout("")
    auth_service.logout("f" * 64)
    auth_service.logout(None)  # type: ignore


def test_revoke_all_sessions(auth_service: AuthService, db_session: Session):
    """Verify revoke_all_sessions invalidates all user sessions while leaving others intact."""
    user_a = auth_service.register(
        full_name="User A",
        email="usera@example.com",
        password="PasswordUserA123!",
    )
    user_b = auth_service.register(
        full_name="User B",
        email="userb@example.com",
        password="PasswordUserB123!",
    )

    # User A has 2 sessions
    token_a1 = auth_service.create_session(user_a.id)
    token_a2 = auth_service.create_session(user_a.id)

    # User B has 1 session
    token_b1 = auth_service.create_session(user_b.id)

    # Revoke all sessions for User A
    auth_service.revoke_all_sessions(user_a.id)

    # Both sessions for User A should now be invalid
    with pytest.raises(InvalidSessionError):
        auth_service.validate_session(token_a1)
    with pytest.raises(InvalidSessionError):
        auth_service.validate_session(token_a2)

    # User B's session should remain completely valid
    validated_b = auth_service.validate_session(token_b1)
    assert validated_b.id == user_b.id

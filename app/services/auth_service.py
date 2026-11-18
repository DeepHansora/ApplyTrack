"""Authentication service implementing business logic for user management and sessions.

Coordinates Argon2id password hashing, timing-resistant authentication,
database-backed session token creation, expiration enforcement, and revocation.
Owns the database transaction boundary (commits on success, rolls back on failure).
"""

from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.core.security import (
    dummy_verify_password,
    generate_session_token,
    hash_password,
    hash_session_token,
    verify_password,
)
from app.models.user import User
from app.models.user_session import UserSession
from app.repositories import session_repository, user_repository
from app.schemas.user import UserCreate


class AuthServiceError(Exception):
    """Base exception for authentication service errors."""

    pass


class DuplicateEmailError(AuthServiceError):
    """Raised when attempting to register with an already registered email."""

    pass


class InvalidCredentialsError(AuthServiceError):
    """Raised when authentication fails due to incorrect credentials or inactive status."""

    pass


class InvalidSessionError(AuthServiceError):
    """Raised when a session token is missing, invalid, expired, revoked, or inactive."""

    pass


class AuthService:
    """Authentication and session management service.

    Orchestrates authentication business logic and owns transaction boundaries
    across UserRepository and SessionRepository.
    """

    def __init__(self, db: Session) -> None:
        """Initialize the AuthService with an active SQLAlchemy session.

        Args:
            db: The active SQLAlchemy database session.
        """
        self.db = db

    def register(
        self,
        user_in: UserCreate | None = None,
        *,
        full_name: str | None = None,
        email: str | None = None,
        password: str | None = None,
        ai_consent: bool = False,
    ) -> User:
        """Register a new user account.

        Accepts either a validated UserCreate Pydantic schema or individual
        registration keyword arguments. Normalizes and validates inputs,
        hashes password with Argon2id, persists AI consent timestamp, creates the user,
        and safely handles concurrent duplicate registration races.

        Args:
            user_in: Optional UserCreate Pydantic schema instance.
            full_name: Optional user display name string.
            email: Optional email address string.
            password: Optional plaintext password string.
            ai_consent: Optional boolean AI consent flag (default: False).

        Returns:
            The newly created and committed User instance.

        Raises:
            DuplicateEmailError: If the email address is already registered or racing.
            ValueError: If required fields are missing, invalid, or whitespace-only.
            IntegrityError: If an unrelated database constraint violation occurs.
        """
        if user_in is not None:
            name = user_in.full_name.strip()
            norm_email = str(user_in.email).strip().lower()
            raw_password = user_in.password
            consent = user_in.ai_consent
            if not name:
                raise ValueError("Full name cannot be empty or whitespace only.")
            if not norm_email:
                raise ValueError("Email cannot be empty or whitespace only.")
            if not raw_password:
                raise ValueError("Password is required.")
        else:
            name = (full_name or "").strip()
            raw_email = (email or "").strip().lower()
            raw_password = password or ""
            consent = ai_consent

            if not name:
                raise ValueError("Full name cannot be empty or whitespace only.")
            if not raw_email:
                raise ValueError("Email cannot be empty or whitespace only.")
            if not raw_password:
                raise ValueError("Password is required.")

            # Validate email format and field constraints using existing UserCreate schema
            user_schema = UserCreate(
                full_name=name,
                email=raw_email,
                password=raw_password,
                ai_consent=consent,
            )
            name = user_schema.full_name
            norm_email = str(user_schema.email)
            raw_password = user_schema.password
            consent = user_schema.ai_consent

        # Check existing user (preliminary lookup)
        existing_user = user_repository.get_by_email(self.db, norm_email)
        if existing_user is not None:
            dummy_verify_password(raw_password)
            raise DuplicateEmailError("An account with this email address already exists.")

        # Hash password with Argon2id (never store plaintext)
        pwd_hash = hash_password(raw_password)

        # AI consent timestamp
        now = datetime.now(timezone.utc)
        consent_time = now if consent else None

        try:
            user = user_repository.create(
                db=self.db,
                full_name=name,
                email=norm_email,
                password_hash=pwd_hash,
                ai_consent=consent,
                ai_consent_given_at=consent_time,
            )
            self.db.commit()
            self.db.refresh(user)
            return user
        except IntegrityError as exc:
            self.db.rollback()
            # If a concurrent registration committed this email, raise DuplicateEmailError
            if user_repository.get_by_email(self.db, norm_email) is not None:
                raise DuplicateEmailError(
                    "An account with this email address already exists."
                ) from exc
            raise
        except Exception:
            self.db.rollback()
            raise

    def authenticate(self, email: str, password: str) -> User:
        """Authenticate user credentials using email and password.

        Uses constant-effort dummy password verification if the email does
        not exist to prevent account enumeration via timing attacks. Rejects
        inactive user accounts with the same generic error message.

        Args:
            email: The login email address.
            password: The plaintext password to verify.

        Returns:
            The authenticated User instance.

        Raises:
            InvalidCredentialsError: If credentials do not match or account is inactive.
        """
        if not email or not password:
            dummy_verify_password(password or "dummy")
            raise InvalidCredentialsError("Invalid email or password.")

        normalized_email = email.strip().lower()
        user = user_repository.get_by_email(self.db, normalized_email)

        if user is None:
            dummy_verify_password(password)
            raise InvalidCredentialsError("Invalid email or password.")

        if not verify_password(password, user.password_hash):
            raise InvalidCredentialsError("Invalid email or password.")

        if not user.is_active:
            raise InvalidCredentialsError("Invalid email or password.")

        return user

    def create_session(self, user_id: UUID) -> str:
        """Create a new database-backed session for a user.

        Generates a 256-bit entropy raw session token, hashes it with SHA-256,
        stores the hash in PostgreSQL with absolute expiration (24h) and
        current activity timestamp, commits the transaction, and returns
        the raw token string to the caller.

        Args:
            user_id: The UUID primary key of the owning user.

        Returns:
            The raw, high-entropy session token string to be stored in an HttpOnly cookie.
        """
        raw_token = generate_session_token()
        token_hash = hash_session_token(raw_token)

        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(hours=settings.SESSION_ABSOLUTE_LIFETIME_HOURS)

        try:
            session_repository.create_session(
                db=self.db,
                user_id=user_id,
                token_hash=token_hash,
                expires_at=expires_at,
            )
            self.db.commit()
            return raw_token
        except Exception:
            self.db.rollback()
            raise

    def _revoke_and_raise(self, token_hash: str, message: str) -> None:
        """Revoke a session in the database, commit the revocation, and raise InvalidSessionError.

        If the revocation or commit fails, rolls back the transaction and re-raises
        the database exception.

        Args:
            token_hash: SHA-256 digest of the session to revoke.
            message: User-facing error message for InvalidSessionError.

        Raises:
            InvalidSessionError: On successful revocation commit.
        """
        try:
            session_repository.revoke_session(self.db, token_hash)
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

        raise InvalidSessionError(message)

    def validate_session(self, raw_token: str) -> User:
        """Validate an active session by its raw token string.

        Hashes the raw token with SHA-256, looks up the active session in PostgreSQL,
        enforces absolute and sliding idle timeouts, verifies user account status,
        and advances last_activity_at timestamp.

        Rolls back the transaction on any database operation or commit failure.
        Preserves committed session revocation when raising InvalidSessionError.

        Args:
            raw_token: The raw session token presented by the browser cookie.

        Returns:
            The authenticated and active User associated with the session.

        Raises:
            InvalidSessionError: If the session is missing, revoked, expired, or inactive.
        """
        if not raw_token or not isinstance(raw_token, str):
            raise InvalidSessionError("Invalid or expired session.")

        try:
            token_hash = hash_session_token(raw_token)
        except ValueError:
            raise InvalidSessionError("Invalid or expired session.")

        try:
            session = session_repository.get_active_session_by_hash(self.db, token_hash)
        except Exception:
            self.db.rollback()
            raise

        if session is None:
            raise InvalidSessionError("Invalid or expired session.")

        now = datetime.now(timezone.utc)

        # 1. Absolute expiration check
        if now >= session.expires_at:
            self._revoke_and_raise(token_hash, "Session has expired.")

        # 2. Idle timeout check (sliding window)
        idle_cutoff = session.last_activity_at + timedelta(minutes=settings.SESSION_IDLE_TIMEOUT_MINUTES)
        if now >= idle_cutoff:
            self._revoke_and_raise(token_hash, "Session expired due to inactivity.")

        # 3. User account status check
        try:
            user = user_repository.get_by_id(self.db, session.user_id)
        except Exception:
            self.db.rollback()
            raise

        if user is None or not user.is_active:
            self._revoke_and_raise(token_hash, "User account is inactive or missing.")

        # 4. Valid session: advance last_activity_at without extending expires_at
        try:
            session_repository.update_last_activity(self.db, session.id)
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

        return user

    def logout(self, raw_token: str) -> None:
        """Revoke a session by its raw token string.

        Idempotent and safe: if token is invalid or missing, completes cleanly.

        Args:
            raw_token: The raw session token string to revoke.
        """
        if not raw_token or not isinstance(raw_token, str):
            return

        try:
            token_hash = hash_session_token(raw_token)
        except ValueError:
            return

        try:
            session_repository.revoke_session(self.db, token_hash)
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    def revoke_all_sessions(self, user_id: UUID) -> None:
        """Revoke all active sessions belonging to the specified user.

        Args:
            user_id: The UUID of the user whose sessions should be invalidated.
        """
        try:
            session_repository.revoke_all_user_sessions(self.db, user_id)
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    def get_session_by_token(self, raw_token: str) -> UserSession | None:
        """Retrieve a session by its raw token for diagnostics or test inspection.

        Args:
            raw_token: The raw session token string.

        Returns:
            The UserSession record or None.
        """
        try:
            token_hash = hash_session_token(raw_token)
        except ValueError:
            return None
        return session_repository.get_active_session_by_hash(self.db, token_hash)

"""Session repository module for database access operations on UserSession entities."""

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.user_session import UserSession


def create_session(
    db: Session,
    user_id: UUID,
    token_hash: str,
    expires_at: datetime,
) -> UserSession:
    """Create and persist a new UserSession record in the active transaction.

    Stores only the SHA-256 token hash, never the raw plaintext session token.
    Flushes the session so database-generated values (UUID primary key, created_at,
    last_activity_at) are populated on the returned instance.

    Does NOT commit the transaction.

    Args:
        db: The active SQLAlchemy session.
        user_id: The UUID primary key of the owning user.
        token_hash: 64-character hexadecimal SHA-256 digest of the session token.
        expires_at: Timezone-aware absolute expiration timestamp.

    Returns:
        The created UserSession instance with populated database attributes.
    """
    session = UserSession(
        user_id=user_id,
        token_hash=token_hash,
        expires_at=expires_at,
    )
    db.add(session)
    db.flush()
    return session


def get_active_session_by_hash(
    db: Session,
    token_hash: str,
) -> UserSession | None:
    """Retrieve an active (non-revoked) session by its token hash.

    Filters out revoked sessions (is_revoked == False). Does not validate
    time-based expiration or idle timeout, which is business logic owned
    by the authentication service layer.

    Args:
        db: The active SQLAlchemy session.
        token_hash: The 64-character SHA-256 digest to look up.

    Returns:
        The matching UserSession instance if found and not revoked, otherwise None.
    """
    if not token_hash or not isinstance(token_hash, str):
        return None

    stmt = select(UserSession).where(
        UserSession.token_hash == token_hash,
        UserSession.is_revoked.is_(False),
    )
    return db.scalar(stmt)


def update_last_activity(db: Session, session_id: UUID) -> None:
    """Update last_activity_at timestamp for the specified session.

    Uses a timezone-aware UTC timestamp. Does NOT modify expires_at,
    ensuring that the absolute session lifetime is never extended by activity.
    Handles missing sessions safely without raising errors.

    Args:
        db: The active SQLAlchemy session.
        session_id: The UUID primary key of the session to update.
    """
    now = datetime.now(timezone.utc)
    stmt = (
        update(UserSession)
        .where(UserSession.id == session_id)
        .values(last_activity_at=now)
    )
    db.execute(stmt)
    db.flush()


def revoke_session(db: Session, token_hash: str) -> None:
    """Atomically mark a session as revoked using its token hash.

    Performs an atomic UPDATE statement setting is_revoked to True.
    Idempotent and safe to call repeatedly. If the session does not exist,
    the operation completes safely.

    Args:
        db: The active SQLAlchemy session.
        token_hash: The 64-character SHA-256 digest of the session to revoke.
    """
    if not token_hash or not isinstance(token_hash, str):
        return

    stmt = (
        update(UserSession)
        .where(UserSession.token_hash == token_hash)
        .values(is_revoked=True)
    )
    db.execute(stmt)
    db.flush()


def revoke_all_user_sessions(db: Session, user_id: UUID) -> None:
    """Atomically revoke all active sessions belonging to the specified user.

    Uses a single UPDATE query filtered strictly by user_id to invalidate
    sessions across all devices (e.g. during password resets or logout-all).
    Does not affect sessions belonging to other users.

    Args:
        db: The active SQLAlchemy session.
        user_id: The UUID of the user whose sessions are to be revoked.
    """
    stmt = (
        update(UserSession)
        .where(UserSession.user_id == user_id, UserSession.is_revoked.is_(False))
        .values(is_revoked=True)
    )
    db.execute(stmt)
    db.flush()

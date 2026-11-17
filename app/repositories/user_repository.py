"""User repository module for database access operations on User entities."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.user import User


def get_by_id(db: Session, user_id: UUID) -> User | None:
    """Retrieve a user by their UUID primary key.

    Args:
        db: The active SQLAlchemy session.
        user_id: The UUID primary key of the user.

    Returns:
        The User instance if found, or None if no matching user exists.
    """
    return db.get(User, user_id)


def get_by_email(db: Session, email: str) -> User | None:
    """Retrieve a user by their email address.

    Normalizes the email by stripping whitespace and converting to lowercase
    prior to querying.

    Args:
        db: The active SQLAlchemy session.
        email: The email address to look up.

    Returns:
        The User instance if found, or None if not found or if email is empty.
    """
    if not email or not isinstance(email, str):
        return None

    normalized_email = email.strip().lower()
    if not normalized_email:
        return None

    stmt = select(User).where(User.email == normalized_email)
    return db.scalar(stmt)


def create(
    db: Session,
    full_name: str,
    email: str,
    password_hash: str,
    ai_consent: bool = False,
    ai_consent_given_at: datetime | None = None,
) -> User:
    """Create and persist a new User record in the active transaction.

    Normalizes email and name, adds the user to the session, and calls flush()
    so that database-generated values (UUID primary key, created_at, updated_at)
    are populated on the returned instance.

    Does NOT commit the transaction. Never accepts or stores plaintext passwords.
    Password hashing must be performed prior to calling this function.

    Args:
        db: The active SQLAlchemy session.
        full_name: The user's full name.
        email: The user's email address.
        password_hash: Precomputed Argon2id password hash string.
        ai_consent: Optional boolean consent for AI resume analysis (default: False).
        ai_consent_given_at: Optional UTC datetime when consent was granted (default: None).

    Returns:
        The created User instance with populated database attributes.
    """
    normalized_name = full_name.strip() if full_name else ""
    normalized_email = email.strip().lower() if email else ""

    user = User(
        full_name=normalized_name,
        email=normalized_email,
        password_hash=password_hash,
        ai_consent=ai_consent,
        ai_consent_given_at=ai_consent_given_at,
    )
    db.add(user)
    db.flush()
    return user

"""SQLAlchemy model for registered user accounts."""

from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.application import Application
    from app.models.company import Company
    from app.models.user_session import UserSession


class User(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """User account entity.
    
    Acts as the central ownership anchor for all user-owned entities
    in ApplyTrack (applications, companies, resumes, sessions, etc.).
    """

    __tablename__ = "users"

    full_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    email: Mapped[str] = mapped_column(
        String(320),
        unique=True,
        index=True,
        nullable=False,
    )
    password_hash: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )
    ai_consent: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    ai_consent_given_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )

    # Relationships
    sessions: Mapped[List["UserSession"]] = relationship(
        "UserSession",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    companies: Mapped[List["Company"]] = relationship(
        "Company",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    applications: Mapped[List["Application"]] = relationship(
        "Application",
        back_populates="user",
        cascade="all, delete-orphan",
    )

    @validates("email")
    def validate_email(self, key: str, value: str) -> str:
        """Normalize email by trimming whitespace and converting to lowercase."""
        if value is not None:
            return value.strip().lower()
        return value

    @validates("full_name")
    def validate_full_name(self, key: str, value: str) -> str:
        """Trim surrounding whitespace from full name."""
        if value is not None:
            return value.strip()
        return value

    def __repr__(self) -> str:
        """Safe representation excluding password_hash."""
        return f"<User id={self.id} email={self.email!r}>"

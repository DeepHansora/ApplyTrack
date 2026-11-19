"""SQLAlchemy model for private user-owned company directory."""

from typing import TYPE_CHECKING, List, Optional
import uuid

from sqlalchemy import Boolean, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.application import Application
    from app.models.user import User


class Company(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Private company directory record, owned by a single user.

    Each user maintains their own company directory. Company names are unique
    per user after normalization (lowercase, trimmed whitespace).
    """

    __tablename__ = "companies"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    name_normalized: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    website: Mapped[Optional[str]] = mapped_column(
        String(2048),
        nullable=True,
    )
    industry: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
    )
    location: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
    )
    notes: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    is_archived: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("user_id", "name_normalized", name="uq_companies_user_name_normalized"),
        Index("idx_companies_user_id_is_archived_name", "user_id", "is_archived", "name"),
    )

    # Relationships
    user: Mapped["User"] = relationship(
        "User",
        back_populates="companies",
    )
    applications: Mapped[List["Application"]] = relationship(
        "Application",
        back_populates="company",
        passive_deletes=True,
    )

    @validates("name")
    def validate_name(self, key: str, value: str) -> str:
        """Trim surrounding whitespace and synchronize name_normalized."""
        if value is not None:
            trimmed = value.strip()
            if not trimmed:
                raise ValueError("Company name cannot be empty or whitespace only.")
            if len(trimmed) > 255:
                raise ValueError("Company name cannot exceed 255 characters.")
            self.name_normalized = trimmed.lower()
            return trimmed
        return value

    @validates("name_normalized")
    def validate_name_normalized(self, key: str, value: str) -> str:
        """Ensure name_normalized is lowercase and trimmed."""
        if value is not None:
            trimmed = value.strip().lower()
            if not trimmed:
                raise ValueError("Normalized company name cannot be empty.")
            return trimmed
        return value

    def __repr__(self) -> str:
        """Safe representation of company state."""
        return f"<Company id={self.id} user_id={self.user_id} name={self.name!r} is_archived={self.is_archived}>"

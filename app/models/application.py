"""SQLAlchemy model for user job applications."""

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional
import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    inspect,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates
from sqlalchemy.orm.attributes import NO_VALUE

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.company import Company
    from app.models.user import User

# Approved status and employment type values
APPLICATION_STATUSES = (
    "Saved",
    "Applied",
    "Assessment",
    "Interview",
    "Offer",
    "Rejected",
    "Withdrawn",
)

EMPLOYMENT_TYPES = (
    "Full-time",
    "Part-time",
    "Contract",
    "Freelance",
    "Internship",
    "Temporary",
)


class Application(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Job application entity representing a candidate's application for a role at a company."""

    __tablename__ = "applications"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    job_title: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    job_description: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    job_url: Mapped[Optional[str]] = mapped_column(
        String(2048),
        nullable=True,
    )
    employment_type: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
    )
    location: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
    )
    salary_min: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(12, 2),
        nullable=True,
    )
    salary_max: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(12, 2),
        nullable=True,
    )
    currency: Mapped[Optional[str]] = mapped_column(
        String(10),
        default="USD",
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(50),
        default="Saved",
        nullable=False,
    )
    applied_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
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
        CheckConstraint(
            f"status IN {APPLICATION_STATUSES}",
            name="ck_applications_status",
        ),
        CheckConstraint(
            f"employment_type IS NULL OR employment_type IN {EMPLOYMENT_TYPES}",
            name="ck_applications_employment_type",
        ),
        CheckConstraint(
            "salary_min IS NULL OR salary_max IS NULL OR salary_min <= salary_max",
            name="ck_applications_salary_range",
        ),
        CheckConstraint(
            "salary_min IS NULL OR salary_min >= 0",
            name="ck_applications_salary_min_non_negative",
        ),
        CheckConstraint(
            "salary_max IS NULL OR salary_max >= 0",
            name="ck_applications_salary_max_non_negative",
        ),
        Index("idx_applications_user_id_status", "user_id", "status"),
        Index("idx_applications_user_id_applied_at", "user_id", "applied_at"),
        Index("idx_applications_company_id", "company_id"),
        Index("idx_applications_user_id_is_archived", "user_id", "is_archived"),
    )

    # Relationships
    user: Mapped["User"] = relationship(
        "User",
        back_populates="applications",
    )
    company: Mapped["Company"] = relationship(
        "Company",
        back_populates="applications",
    )

    @validates("job_title")
    def validate_job_title(self, key: str, value: str) -> str:
        """Trim surrounding whitespace and ensure job title is non-empty."""
        if value is not None:
            trimmed = value.strip()
            if not trimmed:
                raise ValueError("Job title cannot be empty or whitespace only.")
            if len(trimmed) > 255:
                raise ValueError("Job title cannot exceed 255 characters.")
            return trimmed
        return value

    @validates("status")
    def validate_status(self, key: str, value: str) -> str:
        """Validate lifecycle status against allowed set."""
        if value not in APPLICATION_STATUSES:
            raise ValueError(f"Invalid status '{value}'. Must be one of {APPLICATION_STATUSES}.")
        return value

    @validates("employment_type")
    def validate_employment_type(self, key: str, value: Optional[str]) -> Optional[str]:
        """Validate employment type if provided."""
        if value is not None:
            trimmed = value.strip()
            if not trimmed:
                return None
            if trimmed not in EMPLOYMENT_TYPES:
                raise ValueError(f"Invalid employment type '{trimmed}'. Must be one of {EMPLOYMENT_TYPES}.")
            return trimmed
        return value

    @validates("currency")
    def validate_currency(self, key: str, value: Optional[str]) -> Optional[str]:
        """Normalize currency code to uppercase and ensure exactly three alphabetic characters."""
        if value is not None:
            trimmed = value.strip().upper()
            if len(trimmed) != 3 or not trimmed.isalpha():
                raise ValueError("Currency must be exactly three alphabetic characters (e.g., USD, EUR).")
            return trimmed
        return value

    @validates("applied_at")
    def validate_applied_at(self, key: str, value: Optional[datetime]) -> Optional[datetime]:
        """Ensure applied_at is timezone-aware if provided."""
        if value is not None:
            if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
                raise ValueError("applied_at must be a timezone-aware datetime.")
        return value

    @property
    def company_name(self) -> Optional[str]:
        """Return the associated company name if the company relationship is already loaded.

        Uses SQLAlchemy ORM inspection and the NO_VALUE sentinel to avoid triggering
        lazy loading queries or raising DetachedInstanceError on unloaded/detached instances.
        """
        state = inspect(self)
        loaded_company = state.attrs.company.loaded_value
        if loaded_company is not NO_VALUE and loaded_company is not None:
            return loaded_company.name
        return None

    def __repr__(self) -> str:
        """Safe representation of application state."""
        return (
            f"<Application id={self.id} user_id={self.user_id} company_id={self.company_id} "
            f"job_title={self.job_title!r} status={self.status!r} is_archived={self.is_archived}>"
        )

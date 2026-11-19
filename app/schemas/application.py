"""Pydantic schemas for application creation, updates, and responses."""

from datetime import datetime
from decimal import Decimal
from typing import Optional
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.application import APPLICATION_STATUSES, EMPLOYMENT_TYPES


class ApplicationBase(BaseModel):
    """Base application schema with shared optional attributes."""

    job_description: Optional[str] = Field(
        default=None,
        description="Full text job description.",
    )
    job_url: Optional[str] = Field(
        default=None,
        max_length=2048,
        description="Link to original job posting (up to 2048 characters).",
    )
    employment_type: Optional[str] = Field(
        default=None,
        max_length=50,
        description="Employment arrangement (Full-time, Part-time, Contract, etc.).",
    )
    location: Optional[str] = Field(
        default=None,
        max_length=255,
        description="Role location or remote status (up to 255 characters).",
    )
    salary_min: Optional[Decimal] = Field(
        default=None,
        ge=0,
        description="Minimum salary (non-negative).",
    )
    salary_max: Optional[Decimal] = Field(
        default=None,
        ge=0,
        description="Maximum salary (non-negative).",
    )
    currency: Optional[str] = Field(
        default=None,
        max_length=10,
        description="ISO currency code (e.g., USD, EUR).",
    )
    applied_at: Optional[datetime] = Field(
        default=None,
        description="Timezone-aware application submission timestamp.",
    )
    notes: Optional[str] = Field(
        default=None,
        description="Personal candidate notes about the opportunity.",
    )

    @field_validator("job_description", "job_url", "location", "notes", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: Optional[str]) -> Optional[str]:
        """Trim whitespace and normalize empty strings to None."""
        if value is not None and isinstance(value, str):
            trimmed = value.strip()
            return trimmed if trimmed else None
        return value

    @field_validator("employment_type", mode="before")
    @classmethod
    def validate_employment_type(cls, value: Optional[str]) -> Optional[str]:
        """Trim and validate employment type against approved set."""
        if value is not None and isinstance(value, str):
            trimmed = value.strip()
            if not trimmed:
                return None
            if trimmed not in EMPLOYMENT_TYPES:
                raise ValueError(
                    f"Invalid employment type '{trimmed}'. Must be one of: {', '.join(EMPLOYMENT_TYPES)}."
                )
            return trimmed
        return value

    @field_validator("currency", mode="before")
    @classmethod
    def validate_currency(cls, value: Optional[str]) -> Optional[str]:
        """Normalize currency to uppercase and validate as exactly three alphabetic characters."""
        if value is not None and isinstance(value, str):
            trimmed = value.strip().upper()
            if len(trimmed) != 3 or not trimmed.isalpha():
                raise ValueError("Currency must be exactly three alphabetic characters (e.g., USD, EUR).")
            return trimmed
        return value

    @field_validator("applied_at")
    @classmethod
    def validate_applied_at(cls, value: Optional[datetime]) -> Optional[datetime]:
        """Ensure applied_at is timezone-aware with an explicit offset."""
        if value is not None:
            if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
                raise ValueError("applied_at must be timezone-aware with an explicit timezone offset.")
        return value

    @model_validator(mode="after")
    def validate_salary_range(self) -> "ApplicationBase":
        """Validate that salary_min does not exceed salary_max when both are present.

        Note for Slice 2 (ApplicationService): When performing partial updates where only
        salary_min or salary_max is provided, the service layer must validate the merged
        salary range (existing database value + incoming update) against
        effective_min <= effective_max before committing.
        """
        if self.salary_min is not None and self.salary_max is not None:
            if self.salary_min > self.salary_max:
                raise ValueError("Minimum salary cannot exceed maximum salary.")
        return self


class ApplicationCreate(ApplicationBase):
    """Schema for creating a new job application.

    Requires company_id and job_title. Supports initial status selection,
    defaulting to 'Saved'. Currency defaults to 'USD'.
    Server-managed fields are forbidden.
    """

    model_config = ConfigDict(extra="forbid")

    company_id: uuid.UUID = Field(
        ...,
        description="UUID of the user's company this application belongs to.",
    )
    job_title: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Role or job title (1-255 characters).",
    )
    currency: str = Field(
        default="USD",
        description="ISO currency code (default: USD).",
    )
    status: str = Field(
        default="Saved",
        description="Initial lifecycle status (default: Saved).",
    )

    @field_validator("job_title")
    @classmethod
    def validate_job_title(cls, value: str) -> str:
        """Trim surrounding whitespace and ensure job title is non-empty."""
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("Job title cannot be empty or whitespace only.")
        if len(trimmed) > 255:
            raise ValueError("Job title cannot exceed 255 characters.")
        return trimmed

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        """Validate initial status against approved lifecycle statuses."""
        trimmed = value.strip()
        if trimmed not in APPLICATION_STATUSES:
            raise ValueError(
                f"Invalid status '{trimmed}'. Must be one of: {', '.join(APPLICATION_STATUSES)}."
            )
        return trimmed


class ApplicationUpdate(ApplicationBase):
    """Schema for updating an existing job application.

    All fields are optional. Unset fields remain unchanged.
    Status transitions are forbidden here and reserved for Phase 4.
    Archiving/unarchiving is forbidden here and must remain separate operations.
    Ownership (user_id) and server-managed fields are forbidden.

    Note for Slice 2 (ApplicationService): When performing partial updates where only
    salary_min or salary_max is provided, the service layer must validate the merged
    salary range (existing database value + incoming update) against
    effective_min <= effective_max before committing.
    """

    model_config = ConfigDict(extra="forbid")

    company_id: Optional[uuid.UUID] = Field(
        default=None,
        description="Updated company UUID (must belong to the authenticated user).",
    )
    job_title: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="Updated job title (1-255 characters).",
    )

    @field_validator("company_id")
    @classmethod
    def validate_company_id(cls, value: Optional[uuid.UUID]) -> uuid.UUID:
        """Ensure company_id cannot be explicitly set to null."""
        if value is None:
            raise ValueError("Company ID cannot be null.")
        return value

    @field_validator("job_title")
    @classmethod
    def validate_job_title(cls, value: Optional[str]) -> str:
        """Trim whitespace and ensure updated job title is non-empty if provided."""
        if value is None:
            raise ValueError("Job title cannot be null.")
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("Job title cannot be empty or whitespace only.")
        if len(trimmed) > 255:
            raise ValueError("Job title cannot exceed 255 characters.")
        return trimmed


class ApplicationOut(BaseModel):
    """Safe public representation of an application record."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    company_id: uuid.UUID
    job_title: str
    job_description: Optional[str] = None
    job_url: Optional[str] = None
    employment_type: Optional[str] = None
    location: Optional[str] = None
    salary_min: Optional[Decimal] = None
    salary_max: Optional[Decimal] = None
    currency: Optional[str] = "USD"
    status: str
    applied_at: Optional[datetime] = None
    notes: Optional[str] = None
    is_archived: bool
    created_at: datetime
    updated_at: datetime
    company_name: Optional[str] = Field(
        default=None,
        description="Associated company display name, populated from the company relationship when loaded.",
    )

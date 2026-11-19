"""Pydantic schemas for company creation, updates, and responses."""

from datetime import datetime
from typing import Optional
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CompanyBase(BaseModel):
    """Base company schema with shared optional attributes."""

    website: Optional[str] = Field(
        default=None,
        max_length=2048,
        description="Company website URL (up to 2048 characters).",
    )
    industry: Optional[str] = Field(
        default=None,
        max_length=100,
        description="Industry or market sector (up to 100 characters).",
    )
    location: Optional[str] = Field(
        default=None,
        max_length=255,
        description="Office, headquarters, or remote location (up to 255 characters).",
    )
    notes: Optional[str] = Field(
        default=None,
        description="Personal candidate notes about the organization.",
    )

    @field_validator("website", "industry", "location", "notes", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: Optional[str]) -> Optional[str]:
        """Trim whitespace and normalize empty strings to None."""
        if value is not None and isinstance(value, str):
            trimmed = value.strip()
            return trimmed if trimmed else None
        return value


class CompanyCreate(CompanyBase):
    """Schema for creating a new company record.

    Ownership (user_id) and timestamps are server-managed and forbidden here.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Company display name (1-255 characters).",
    )

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        """Trim surrounding whitespace and ensure company name is non-empty."""
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("Company name cannot be empty or whitespace only.")
        if len(trimmed) > 255:
            raise ValueError("Company name cannot exceed 255 characters.")
        return trimmed


class CompanyUpdate(CompanyBase):
    """Schema for updating an existing company record.

    All fields are optional. Unset fields remain unchanged.
    Ownership (user_id) and server-managed fields are forbidden.
    """

    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="Updated company display name (1-255 characters).",
    )
    is_archived: Optional[bool] = Field(
        default=None,
        description="Whether the company is archived (soft-deleted).",
    )

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: Optional[str]) -> str:
        """Trim surrounding whitespace and ensure updated name is non-empty if provided."""
        if value is None:
            raise ValueError("Company name cannot be null.")
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("Company name cannot be empty or whitespace only.")
        if len(trimmed) > 255:
            raise ValueError("Company name cannot exceed 255 characters.")
        return trimmed


class CompanyOut(BaseModel):
    """Safe public representation of a company record.

    Excludes internal implementation details such as name_normalized.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    name: str
    website: Optional[str] = None
    industry: Optional[str] = None
    location: Optional[str] = None
    notes: Optional[str] = None
    is_archived: bool
    created_at: datetime
    updated_at: datetime

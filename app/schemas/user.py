"""Pydantic schemas for user registration, authentication, and responses."""

from datetime import datetime
from typing import Optional
import uuid

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


def _normalize_email(value: str) -> str:
    """Normalize email by stripping whitespace and converting to lowercase."""
    return value.strip().lower()


class UserCreate(BaseModel):
    """Schema for new user registration requests."""

    full_name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="User display name (1-255 characters).",
    )
    email: EmailStr = Field(
        ...,
        description="User email address used as the login identifier.",
    )
    password: str = Field(
        ...,
        min_length=8,
        max_length=128,
        description="Plaintext password to be hashed with Argon2id (8-128 characters).",
    )
    ai_consent: bool = Field(
        default=False,
        description="User consent for optional AI-assisted resume matching.",
    )

    @field_validator("full_name")
    @classmethod
    def validate_full_name(cls, value: str) -> str:
        """Trim surrounding whitespace and ensure name is non-empty."""
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("Full name cannot be empty or whitespace only.")
        if len(trimmed) > 255:
            raise ValueError("Full name cannot exceed 255 characters.")
        return trimmed

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        """Trim whitespace and convert email to lowercase."""
        return _normalize_email(value)


class UserLogin(BaseModel):
    """Schema for user authentication / login requests."""

    email: EmailStr = Field(
        ...,
        description="User email address used as the login identifier.",
    )
    password: str = Field(
        ...,
        min_length=8,
        max_length=128,
        description="Plaintext password to verify against stored Argon2id hash.",
    )

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        """Trim whitespace and convert email to lowercase."""
        return _normalize_email(value)


class UserOut(BaseModel):
    """Safe public representation of a user account.
    
    Excludes sensitive attributes such as password and password_hash.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    full_name: str
    email: EmailStr
    is_active: bool
    ai_consent: bool
    ai_consent_given_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

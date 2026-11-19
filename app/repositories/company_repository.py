"""Company repository module for database access operations on Company entities."""

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.company import Company

# Fields permitted for partial client updates via repository.
# Server-managed fields (id, user_id, created_at, updated_at, name_normalized) cannot be updated here.
ALLOWED_UPDATE_FIELDS = frozenset(
    {"name", "website", "industry", "location", "notes", "is_archived"}
)


def create(
    db: Session,
    user_id: UUID,
    name: str,
    website: str | None = None,
    industry: str | None = None,
    location: str | None = None,
    notes: str | None = None,
) -> Company:
    """Create and persist a new Company record in the active transaction.

    Sets ownership to the user_id provided by the trusted service layer.
    Trims whitespace and synchronizes name_normalized automatically via the model.
    Adds the record to the session and calls flush() to populate database defaults
    (UUID primary key, created_at, updated_at, name_normalized).

    Does NOT commit the transaction.

    Args:
        db: The active SQLAlchemy database session.
        user_id: The UUID primary key of the owning user.
        name: The display name of the company (1-255 characters).
        website: Optional company website URL.
        industry: Optional industry or market sector.
        location: Optional office or headquarters location.
        notes: Optional user notes about the organization.

    Returns:
        The newly created Company instance.
    """
    company = Company(
        user_id=user_id,
        name=name,
        website=website,
        industry=industry,
        location=location,
        notes=notes,
    )
    db.add(company)
    db.flush()
    return company


def get_by_id(
    db: Session,
    company_id: UUID,
    user_id: UUID,
) -> Company | None:
    """Retrieve a company by its ID and owner ID.

    Guarantees tenant isolation by filtering both on primary key and user_id.
    Returns None if the company does not exist or is owned by another user.

    Args:
        db: The active SQLAlchemy database session.
        company_id: The UUID primary key of the company.
        user_id: The UUID primary key of the owning user.

    Returns:
        The matching Company instance if found and owned by user_id, else None.
    """
    stmt = select(Company).where(
        Company.id == company_id,
        Company.user_id == user_id,
    )
    return db.scalar(stmt)


def get_by_name_normalized(
    db: Session,
    user_id: UUID,
    name_normalized: str,
) -> Company | None:
    """Retrieve a company by its owner ID and normalized name.

    Used for preliminary duplicate detection and case-insensitive lookups.

    Args:
        db: The active SQLAlchemy database session.
        user_id: The UUID primary key of the owning user.
        name_normalized: The lowercase, trimmed company name to search for.

    Returns:
        The matching Company instance if found, else None.
    """
    if not name_normalized or not isinstance(name_normalized, str):
        return None

    normalized = name_normalized.strip().lower()
    if not normalized:
        return None

    stmt = select(Company).where(
        Company.user_id == user_id,
        Company.name_normalized == normalized,
    )
    return db.scalar(stmt)


def list_by_user(
    db: Session,
    user_id: UUID,
    is_archived: bool | None = None,
) -> list[Company]:
    """List companies belonging to a specific user with optional archive filtering.

    Always orders results alphabetically by company name ascending (A-Z).
    Leverages the covering index idx_companies_user_id_is_archived_name.

    Args:
        db: The active SQLAlchemy database session.
        user_id: The UUID primary key of the owning user.
        is_archived: Optional boolean filter:
            - True: return only archived companies.
            - False: return only active companies.
            - None: return all companies (both active and archived).

    Returns:
        A list of Company instances belonging to the specified user.
    """
    stmt = select(Company).where(Company.user_id == user_id)
    if is_archived is not None:
        stmt = stmt.where(Company.is_archived == is_archived)
    stmt = stmt.order_by(Company.name.asc())
    return list(db.scalars(stmt).all())


def update(
    db: Session,
    company_id: UUID,
    user_id: UUID,
    update_data: dict[str, Any],
) -> Company | None:
    """Update a company's attributes only when its ID and owner ID match.

    Applies only explicitly supplied fields in update_data that are present
    in ALLOWED_UPDATE_FIELDS. Updates on the model instance ensure that setting
    the name attribute automatically synchronizes name_normalized via model
    validators. Does NOT allow updates to ownership or primary keys.

    Flushes changes to the database. Does NOT commit the transaction.

    Args:
        db: The active SQLAlchemy database session.
        company_id: The UUID primary key of the company to update.
        user_id: The UUID primary key of the owning user.
        update_data: Dictionary of field names and new values to update.

    Returns:
        The updated Company instance if found and updated, or None if not found
        or owned by another user.
    """
    company = get_by_id(db, company_id, user_id)
    if company is None:
        return None

    for key, value in update_data.items():
        if key in ALLOWED_UPDATE_FIELDS:
            setattr(company, key, value)

    db.flush()
    return company


def archive(
    db: Session,
    company_id: UUID,
    user_id: UUID,
) -> Company | None:
    """Archive a company by setting is_archived to True if ID and owner ID match.

    Preserves the record in the database. Idempotent if already archived.
    Flushes changes to the database. Does NOT commit the transaction.

    Args:
        db: The active SQLAlchemy database session.
        company_id: The UUID primary key of the company to archive.
        user_id: The UUID primary key of the owning user.

    Returns:
        The archived Company instance if found, or None if not found or
        owned by another user.
    """
    company = get_by_id(db, company_id, user_id)
    if company is None:
        return None

    company.is_archived = True
    db.flush()
    return company

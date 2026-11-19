"""Application repository module for database access operations on Application entities."""

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, contains_eager, joinedload

from app.models.application import Application
from app.models.company import Company

# Fields permitted for partial client updates via repository.
# Server-managed and lifecycle-restricted fields (id, user_id, status, is_archived,
# created_at, updated_at) cannot be updated here.
ALLOWED_UPDATE_FIELDS = frozenset({
    "company_id",
    "job_title",
    "job_description",
    "job_url",
    "employment_type",
    "location",
    "salary_min",
    "salary_max",
    "currency",
    "applied_at",
    "notes",
})

# Whitelist of allowed sort fields to prevent SQL injection and guarantee predictability.
SORT_FIELDS = {
    "created_at": Application.created_at,
    "created": Application.created_at,
    "date": Application.created_at,
    "applied_at": Application.applied_at,
    "applied_date": Application.applied_at,
    "applied": Application.applied_at,
    "job_title": Application.job_title,
    "role": Application.job_title,
    "title": Application.job_title,
    "company_name": Company.name,
    "company": Company.name,
    "salary": Application.salary_max,
    "salary_max": Application.salary_max,
    "salary_min": Application.salary_min,
    "status": Application.status,
}


def create(
    db: Session,
    user_id: UUID,
    company_id: UUID,
    job_title: str,
    job_description: str | None = None,
    job_url: str | None = None,
    employment_type: str | None = None,
    location: str | None = None,
    salary_min: Decimal | None = None,
    salary_max: Decimal | None = None,
    currency: str | None = "USD",
    status: str = "Saved",
    applied_at: datetime | None = None,
    notes: str | None = None,
) -> Application:
    """Create and persist a new Application record in the active transaction.

    Sets ownership to user_id provided by the trusted service layer.
    Adds the record to the session and calls flush() to populate database defaults
    (UUID primary key, created_at, updated_at).

    Does NOT commit the transaction.

    Args:
        db: The active SQLAlchemy database session.
        user_id: The UUID primary key of the owning user.
        company_id: The UUID primary key of the company this application belongs to.
        job_title: The role or job title (1-255 characters).
        job_description: Optional full job description text.
        job_url: Optional URL to job posting.
        employment_type: Optional employment arrangement.
        location: Optional job location.
        salary_min: Optional minimum salary.
        salary_max: Optional maximum salary.
        currency: Optional 3-letter currency code (default: USD).
        status: Initial lifecycle status (default: Saved).
        applied_at: Optional timezone-aware application timestamp.
        notes: Optional candidate notes.

    Returns:
        The newly created Application instance.
    """
    application = Application(
        user_id=user_id,
        company_id=company_id,
        job_title=job_title,
        job_description=job_description,
        job_url=job_url,
        employment_type=employment_type,
        location=location,
        salary_min=salary_min,
        salary_max=salary_max,
        currency=currency,
        status=status,
        applied_at=applied_at,
        notes=notes,
    )
    db.add(application)
    db.flush()
    return application


def get_by_id(
    db: Session,
    application_id: UUID,
    user_id: UUID,
) -> Application | None:
    """Retrieve an application by its ID and owner ID.

    Guarantees tenant isolation by filtering both on primary key and user_id.
    Eagerly loads the associated Company so company_name is immediately available.
    Returns None if the application does not exist or is owned by another user.

    Args:
        db: The active SQLAlchemy database session.
        application_id: The UUID primary key of the application.
        user_id: The UUID primary key of the owning user.

    Returns:
        The matching Application instance if found and owned by user_id, else None.
    """
    stmt = (
        select(Application)
        .options(joinedload(Application.company))
        .where(
            Application.id == application_id,
            Application.user_id == user_id,
        )
    )
    return db.scalar(stmt)


def list_by_user(
    db: Session,
    user_id: UUID,
    is_archived: bool | None = False,
) -> list[Application]:
    """Retrieve all applications belonging to a user with optional archive filter.

    Eagerly loads the associated Company relationship.
    Orders results by created_at descending.

    Args:
        db: The active SQLAlchemy database session.
        user_id: The UUID primary key of the owning user.
        is_archived: Optional boolean filter:
            - False (default): active applications only.
            - True: archived applications only.
            - None: all applications (active and archived).

    Returns:
        A list of Application instances belonging to the specified user.
    """
    stmt = (
        select(Application)
        .options(joinedload(Application.company))
        .where(Application.user_id == user_id)
    )
    if is_archived is not None:
        stmt = stmt.where(Application.is_archived == is_archived)
    stmt = stmt.order_by(Application.created_at.desc(), Application.id.desc())
    return list(db.scalars(stmt).all())


def list_applications(
    db: Session,
    user_id: UUID,
    search: str | None = None,
    status: str | None = None,
    company_id: UUID | None = None,
    employment_type: str | None = None,
    applied_after: datetime | None = None,
    applied_before: datetime | None = None,
    is_archived: bool | None = False,
    sort_by: str = "created_at",
    sort_order: str = "desc",
    page: int = 1,
    per_page: int = 20,
) -> tuple[list[Application], int]:
    """List, search, filter, sort, and paginate applications for a user.

    Joins Company for company name search and eager loading.
    Returns a tuple of (items, total_count).

    Does NOT commit or rollback the transaction.

    Args:
        db: The active SQLAlchemy database session.
        user_id: The UUID primary key of the owning user.
        search: Optional search term matching job_title or company.name case-insensitively.
        status: Optional lifecycle status filter.
        company_id: Optional company UUID filter.
        employment_type: Optional employment type filter.
        applied_after: Optional minimum applied_at timestamp.
        applied_before: Optional maximum applied_at timestamp.
        is_archived: Optional archive filter (default: False for active only).
        sort_by: Column to sort by (from SORT_FIELDS whitelist, default: created_at).
        sort_order: Sort direction ('asc' or 'desc', default: desc).
        page: 1-based page number (default: 1).
        per_page: Number of items per page (5, 10, or 20, default: 20).

    Returns:
        Tuple of (list of Application instances, total matching count).
    """
    # 1. Base filter conditions scoped to current user
    conditions = [Application.user_id == user_id]

    # Search filter across job_title and company.name
    if search and isinstance(search, str):
        search_term = search.strip()
        if search_term:
            pattern = f"%{search_term}%"
            conditions.append(
                or_(
                    Application.job_title.ilike(pattern),
                    Company.name.ilike(pattern),
                )
            )

    # Status filter
    if status and isinstance(status, str):
        status_val = status.strip()
        if status_val:
            conditions.append(Application.status == status_val)

    # Company ID filter
    if company_id is not None:
        conditions.append(Application.company_id == company_id)

    # Employment type filter
    if employment_type and isinstance(employment_type, str):
        emp_val = employment_type.strip()
        if emp_val:
            conditions.append(Application.employment_type == emp_val)

    # Applied date range filters
    if applied_after is not None:
        conditions.append(Application.applied_at >= applied_after)
    if applied_before is not None:
        conditions.append(Application.applied_at <= applied_before)

    # Archive filter
    if is_archived is not None:
        conditions.append(Application.is_archived == is_archived)

    # 2. Total count query
    count_stmt = (
        select(func.count(Application.id))
        .join(Company, Application.company_id == Company.id)
        .where(*conditions)
    )
    total_count = db.scalar(count_stmt) or 0

    # 3. Pagination sanitization
    if page < 1:
        page = 1
    if per_page not in (5, 10, 20):
        per_page = 20
    offset = (page - 1) * per_page

    # 4. Sorting with whitelist and secondary tie-breaker for stable pagination
    sort_key = sort_by.lower() if sort_by else "created_at"
    sort_column = SORT_FIELDS.get(sort_key, Application.created_at)
    order_clause = sort_column.asc() if sort_order and sort_order.lower() == "asc" else sort_column.desc()

    # 5. Data query with eager loading
    data_stmt = (
        select(Application)
        .join(Company, Application.company_id == Company.id)
        .options(contains_eager(Application.company))
        .where(*conditions)
        .order_by(order_clause, Application.id.desc())
        .offset(offset)
        .limit(per_page)
    )
    items = list(db.scalars(data_stmt).all())

    return items, total_count


def update(
    db: Session,
    application_id: UUID,
    user_id: UUID,
    update_data: dict[str, Any],
) -> Application | None:
    """Update an application's attributes only when its ID and owner ID match.

    Applies only explicitly supplied fields in update_data that are present
    in ALLOWED_UPDATE_FIELDS. Does NOT permit updates to user_id, status, or is_archived.

    Flushes changes to the database. Does NOT commit the transaction.

    Args:
        db: The active SQLAlchemy database session.
        application_id: The UUID primary key of the application to update.
        user_id: The UUID primary key of the owning user.
        update_data: Dictionary of field names and new values to update.

    Returns:
        The updated Application instance if found and updated, or None if not found
        or owned by another user.
    """
    application = get_by_id(db, application_id, user_id)
    if application is None:
        return None

    for key, value in update_data.items():
        if key in ALLOWED_UPDATE_FIELDS:
            setattr(application, key, value)

    db.flush()
    return application


def archive(
    db: Session,
    application_id: UUID,
    user_id: UUID,
) -> Application | None:
    """Archive an application by setting is_archived to True if ID and owner ID match.

    Preserves the record in the database. Idempotent if already archived.
    Flushes changes to the database. Does NOT commit the transaction.

    Args:
        db: The active SQLAlchemy database session.
        application_id: The UUID primary key of the application to archive.
        user_id: The UUID primary key of the owning user.

    Returns:
        The archived Application instance if found, or None if not found or
        owned by another user.
    """
    application = get_by_id(db, application_id, user_id)
    if application is None:
        return None

    application.is_archived = True
    db.flush()
    return application


def unarchive(
    db: Session,
    application_id: UUID,
    user_id: UUID,
) -> Application | None:
    """Unarchive an application by setting is_archived to False if ID and owner ID match.

    Preserves the record in the database. Idempotent if already active.
    Flushes changes to the database. Does NOT commit the transaction.

    Args:
        db: The active SQLAlchemy database session.
        application_id: The UUID primary key of the application to unarchive.
        user_id: The UUID primary key of the owning user.

    Returns:
        The unarchived Application instance if found, or None if not found or
        owned by another user.
    """
    application = get_by_id(db, application_id, user_id)
    if application is None:
        return None

    application.is_archived = False
    db.flush()
    return application

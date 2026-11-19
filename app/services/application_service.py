"""Application service implementing business logic, validations, and transaction boundaries.

Coordinates application creation, retrieval, updates, filtering/search/pagination,
and archiving with strict multi-tenant user isolation. Owns database transaction
boundaries (commits on success, rolls back on failure).
"""

from datetime import datetime
from decimal import Decimal
import math
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.application import Application
from app.repositories import application_repository, company_repository
from app.schemas.application import ApplicationCreate, ApplicationUpdate


class ApplicationServiceError(Exception):
    """Base exception for all application service errors."""

    pass


class ApplicationNotFoundError(ApplicationServiceError):
    """Raised when an application is not found or does not belong to the requesting user."""

    pass


class InvalidApplicationCompanyError(ApplicationServiceError):
    """Raised when an application references a company not owned by the user or an inactive company."""

    pass


class InvalidSalaryRangeError(ApplicationServiceError):
    """Raised when salary values are invalid or salary_min exceeds salary_max."""

    pass


class InvalidApplicationUpdateError(ApplicationServiceError):
    """Raised when an update payload is invalid, empty, or contains forbidden fields."""

    pass


class ApplicationService:
    """Service handling job application business logic and transaction boundaries."""

    def __init__(self, db: Session) -> None:
        """Initialize ApplicationService with an active SQLAlchemy database session.

        Args:
            db: The active SQLAlchemy database session.
        """
        self.db = db

    def create_application(
        self,
        user_id: UUID,
        application_in: ApplicationCreate | None = None,
        **kwargs: Any,
    ) -> Application:
        """Create a new job application owned by the authenticated user.

        Accepts either a validated ApplicationCreate Pydantic schema or individual
        attribute keyword arguments. Enforces that:
        1. The target company exists, belongs to the authenticated user, and is active (not archived).
        2. Ownership (user_id) is anchored strictly to the authenticated user.
        3. Salary range (if provided) is non-negative and min <= max.
        4. Status defaults to 'Saved' unless an initial allowed status is specified.

        Commits on success; rolls back on failure.

        Args:
            user_id: The authenticated user's UUID primary key.
            application_in: Optional ApplicationCreate Pydantic schema instance.
            **kwargs: Field arguments used to construct ApplicationCreate if application_in is omitted.

        Returns:
            The newly created and committed Application instance.

        Raises:
            InvalidApplicationCompanyError: If company does not exist, belongs to another user,
                or is archived.
            InvalidSalaryRangeError: If salary_min exceeds salary_max or values are negative.
            pydantic.ValidationError: If field inputs fail schema validation.
            ApplicationServiceError: On other database or service failures.
        """
        if application_in is None:
            application_in = ApplicationCreate(**kwargs)

        # 1. Company ownership and active status validation
        company = company_repository.get_by_id(
            db=self.db,
            company_id=application_in.company_id,
            user_id=user_id,
        )
        if company is None:
            raise InvalidApplicationCompanyError(
                "Selected company not found or does not belong to the current user."
            )
        if company.is_archived:
            raise InvalidApplicationCompanyError(
                "Cannot associate a new application with an archived company."
            )

        # 2. Salary range validation
        if application_in.salary_min is not None and application_in.salary_max is not None:
            if application_in.salary_min > application_in.salary_max:
                raise InvalidSalaryRangeError(
                    f"Minimum salary ({application_in.salary_min}) cannot exceed maximum salary ({application_in.salary_max})."
                )

        try:
            application = application_repository.create(
                db=self.db,
                user_id=user_id,
                company_id=application_in.company_id,
                job_title=application_in.job_title,
                job_description=application_in.job_description,
                job_url=application_in.job_url,
                employment_type=application_in.employment_type,
                location=application_in.location,
                salary_min=application_in.salary_min,
                salary_max=application_in.salary_max,
                currency=application_in.currency,
                status=application_in.status,
                applied_at=application_in.applied_at,
                notes=application_in.notes,
            )
            self.db.commit()
            self.db.refresh(application)
            return application
        except Exception:
            self.db.rollback()
            raise

    def get_application(
        self,
        application_id: UUID,
        user_id: UUID,
    ) -> Application:
        """Retrieve an application by its ID, ensuring it belongs to the authenticated user.

        Treats an application belonging to another user as not found to prevent
        account enumeration and IDOR disclosures.

        Args:
            application_id: The UUID primary key of the application.
            user_id: The UUID primary key of the authenticated user.

        Returns:
            The matching Application instance with loaded company relationship.

        Raises:
            ApplicationNotFoundError: If the application does not exist or is not owned by user_id.
        """
        try:
            application = application_repository.get_by_id(
                db=self.db,
                application_id=application_id,
                user_id=user_id,
            )
        except Exception:
            self.db.rollback()
            raise

        if application is None:
            raise ApplicationNotFoundError(f"Application '{application_id}' not found.")
        return application

    def list_applications(
        self,
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
    ) -> dict[str, Any]:
        """List, search, filter, sort, and paginate applications for the authenticated user.

        Enforces:
        - Strict user isolation.
        - Valid date range (applied_after <= applied_before).
        - Sanitized pagination limits (page >= 1, per_page in 5, 10, or 20).
        - Whitelisted sort fields.

        Args:
            user_id: The UUID primary key of the authenticated user.
            search: Optional text matching job_title or company.name.
            status: Optional lifecycle status filter.
            company_id: Optional company UUID filter.
            employment_type: Optional employment type filter.
            applied_after: Optional minimum applied_at timestamp.
            applied_before: Optional maximum applied_at timestamp.
            is_archived: Optional archive filter (default: False for active applications only).
            sort_by: Field to sort by (from approved whitelist, default: created_at).
            sort_order: Sort direction ('asc' or 'desc', default: desc).
            page: 1-based page number (default: 1).
            per_page: Items per page (5, 10, or 20, default: 20).

        Returns:
            Dictionary containing:
            - "items": list of Application instances for the requested page.
            - "total": total count of matching applications.
            - "page": current page number.
            - "per_page": current per-page limit.
            - "total_pages": calculated total number of pages.

        Raises:
            ApplicationServiceError: If date range is inverted (applied_after > applied_before).
        """
        if applied_after is not None and applied_before is not None:
            if applied_after > applied_before:
                raise ApplicationServiceError(
                    "applied_after date cannot be after applied_before date."
                )

        if page < 1:
            page = 1
        if per_page not in (5, 10, 20):
            per_page = 20

        sort_key = sort_by.lower() if sort_by else "created_at"
        if sort_key not in application_repository.SORT_FIELDS:
            sort_key = "created_at"

        sort_dir = sort_order.lower() if sort_order else "desc"
        if sort_dir not in ("asc", "desc"):
            sort_dir = "desc"

        try:
            items, total_count = application_repository.list_applications(
                db=self.db,
                user_id=user_id,
                search=search,
                status=status,
                company_id=company_id,
                employment_type=employment_type,
                applied_after=applied_after,
                applied_before=applied_before,
                is_archived=is_archived,
                sort_by=sort_key,
                sort_order=sort_dir,
                page=page,
                per_page=per_page,
            )
        except Exception:
            self.db.rollback()
            raise

        total_pages = max(1, math.ceil(total_count / per_page)) if total_count > 0 else 1

        return {
            "items": items,
            "total": total_count,
            "page": page,
            "per_page": per_page,
            "total_pages": total_pages,
        }

    def update_application(
        self,
        application_id: UUID,
        user_id: UUID,
        application_in: ApplicationUpdate | None = None,
        **kwargs: Any,
    ) -> Application:
        """Update an existing job application owned by the authenticated user.

        Applies only explicitly provided fields from ApplicationUpdate.
        Enforces business rules:
        1. Preserves existing company association when company_id is omitted or unchanged,
           even if that company has since been archived.
        2. If company_id is changed to a different company, verifies that new company
           is owned by the current user and active (not archived).
        3. Merges incoming salary fields with existing database values before validating
           that effective_min <= effective_max and non-negative.
        4. Forbids status transitions (reserved for Phase 4) and archiving (dedicated operations).

        Commits on success; rolls back on failure.

        Args:
            application_id: The UUID primary key of the application to update.
            user_id: The UUID primary key of the authenticated user.
            application_in: Optional ApplicationUpdate Pydantic schema instance.
            **kwargs: Field arguments used to construct ApplicationUpdate if application_in is omitted.

        Returns:
            The updated and committed Application instance.

        Raises:
            ApplicationNotFoundError: If the application does not exist or is not owned by user_id.
            InvalidApplicationUpdateError: If no fields were provided for update.
            InvalidApplicationCompanyError: If changed company is not found, owned by another user,
                or archived.
            InvalidSalaryRangeError: If merged salary range is invalid or negative.
            pydantic.ValidationError: If field inputs fail schema validation.
            ApplicationServiceError: On other database or service failures.
        """
        if application_in is None:
            application_in = ApplicationUpdate(**kwargs)

        existing_app = self.get_application(application_id=application_id, user_id=user_id)

        update_data = application_in.model_dump(exclude_unset=True)
        if not update_data:
            raise InvalidApplicationUpdateError("At least one field must be provided for update.")

        # 1. Company Association Validation on Edit
        if "company_id" in update_data:
            new_company_id = update_data["company_id"]
            if new_company_id != existing_app.company_id:
                # User explicitly selected a different company: validate it is owned and active
                new_company = company_repository.get_by_id(
                    db=self.db,
                    company_id=new_company_id,
                    user_id=user_id,
                )
                if new_company is None:
                    raise InvalidApplicationCompanyError(
                        "Selected company not found or does not belong to the current user."
                    )
                if new_company.is_archived:
                    raise InvalidApplicationCompanyError(
                        "Cannot change application association to an archived company."
                    )

        # 2. Merged Salary Validation
        effective_min = (
            update_data["salary_min"]
            if "salary_min" in update_data
            else existing_app.salary_min
        )
        effective_max = (
            update_data["salary_max"]
            if "salary_max" in update_data
            else existing_app.salary_max
        )

        if effective_min is not None and effective_max is not None:
            if effective_min > effective_max:
                raise InvalidSalaryRangeError(
                    f"Minimum salary ({effective_min}) cannot exceed maximum salary ({effective_max})."
                )
        if effective_min is not None and effective_min < 0:
            raise InvalidSalaryRangeError("Minimum salary cannot be negative.")
        if effective_max is not None and effective_max < 0:
            raise InvalidSalaryRangeError("Maximum salary cannot be negative.")

        try:
            updated = application_repository.update(
                db=self.db,
                application_id=application_id,
                user_id=user_id,
                update_data=update_data,
            )
            if updated is None:
                raise ApplicationNotFoundError(f"Application '{application_id}' not found.")
            self.db.commit()
            self.db.refresh(updated)
            return updated
        except Exception:
            self.db.rollback()
            raise

    def archive_application(
        self,
        application_id: UUID,
        user_id: UUID,
    ) -> Application:
        """Archive an application owned by the authenticated user.

        Preserves the record in the database by setting is_archived to True.
        Safe and idempotent if already archived.

        Commits on success; rolls back on failure.

        Args:
            application_id: The UUID primary key of the application to archive.
            user_id: The UUID primary key of the authenticated user.

        Returns:
            The archived and committed Application instance.

        Raises:
            ApplicationNotFoundError: If the application does not exist or is not owned by user_id.
            ApplicationServiceError: On other database or service failures.
        """
        existing_app = self.get_application(application_id=application_id, user_id=user_id)
        if existing_app.is_archived:
            return existing_app

        try:
            archived = application_repository.archive(
                db=self.db,
                application_id=application_id,
                user_id=user_id,
            )
            if archived is None:
                raise ApplicationNotFoundError(f"Application '{application_id}' not found.")
            self.db.commit()
            self.db.refresh(archived)
            return archived
        except Exception:
            self.db.rollback()
            raise

    def unarchive_application(
        self,
        application_id: UUID,
        user_id: UUID,
    ) -> Application:
        """Unarchive an application owned by the authenticated user.

        Preserves the record in the database by restoring is_archived to False.
        Safe and idempotent if already active.

        Commits on success; rolls back on failure.

        Args:
            application_id: The UUID primary key of the application to unarchive.
            user_id: The UUID primary key of the authenticated user.

        Returns:
            The unarchived and committed Application instance.

        Raises:
            ApplicationNotFoundError: If the application does not exist or is not owned by user_id.
            ApplicationServiceError: On other database or service failures.
        """
        existing_app = self.get_application(application_id=application_id, user_id=user_id)
        if not existing_app.is_archived:
            return existing_app

        try:
            unarchived = application_repository.unarchive(
                db=self.db,
                application_id=application_id,
                user_id=user_id,
            )
            if unarchived is None:
                raise ApplicationNotFoundError(f"Application '{application_id}' not found.")
            self.db.commit()
            self.db.refresh(unarchived)
            return unarchived
        except Exception:
            self.db.rollback()
            raise

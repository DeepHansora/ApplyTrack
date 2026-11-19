"""Company service implementing business logic and transaction boundaries.

Coordinates company creation, retrieval, updates, and archiving with strict
multi-tenant user isolation. Owns the database transaction boundary
(commits on success, rolls back on failure).
"""

from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.company import Company
from app.repositories import company_repository
from app.schemas.company import CompanyCreate, CompanyUpdate


class CompanyServiceError(Exception):
    """Base exception for all company service errors."""

    pass


class CompanyNotFoundError(CompanyServiceError):
    """Raised when a company is not found or does not belong to the requesting user."""

    pass


class DuplicateCompanyNameError(CompanyServiceError):
    """Raised when a company name duplicates an existing company for the same user."""

    pass


class InvalidCompanyUpdateError(CompanyServiceError):
    """Raised when an update payload is invalid or contains no updatable fields."""

    pass


class CompanyService:
    """Service handling company directory business logic and transaction boundaries."""

    def __init__(self, db: Session) -> None:
        """Initialize the CompanyService with an active SQLAlchemy session.

        Args:
            db: The active SQLAlchemy database session.
        """
        self.db = db

    def create_company(
        self,
        user_id: UUID,
        company_in: CompanyCreate | None = None,
        **kwargs: Any,
    ) -> Company:
        """Create a new company record owned by the authenticated user.

        Accepts either a validated CompanyCreate Pydantic schema or individual
        company attribute keyword arguments. Ensures ownership is bound strictly
        to the authenticated user_id. Detects duplicate normalized company names
        for the same user and handles concurrent creation races gracefully.

        Commits on success; rolls back on failure.

        Args:
            user_id: The authenticated user's UUID primary key.
            company_in: Optional CompanyCreate Pydantic schema instance.
            **kwargs: Field arguments used to construct CompanyCreate if company_in is omitted.

        Returns:
            The newly created and committed Company instance.

        Raises:
            DuplicateCompanyNameError: If a company with the same normalized name
                already exists for this user.
            pydantic.ValidationError: If field inputs fail schema validation.
            CompanyServiceError: On other database or service failures.
        """
        if company_in is None:
            company_in = CompanyCreate(**kwargs)

        normalized_name = company_in.name.strip().lower()

        # Preliminary check for existing company with identical normalized name for this user
        try:
            existing = company_repository.get_by_name_normalized(
                db=self.db,
                user_id=user_id,
                name_normalized=normalized_name,
            )
        except Exception:
            self.db.rollback()
            raise

        if existing is not None:
            raise DuplicateCompanyNameError(
                f"A company named '{company_in.name}' already exists."
            )

        try:
            company = company_repository.create(
                db=self.db,
                user_id=user_id,
                name=company_in.name,
                website=company_in.website,
                industry=company_in.industry,
                location=company_in.location,
                notes=company_in.notes,
            )
            self.db.commit()
            self.db.refresh(company)
            return company
        except IntegrityError as exc:
            self.db.rollback()
            # Handle concurrent race where another request created the same company name
            if "uq_companies_user_name_normalized" in str(exc).lower() or (
                company_repository.get_by_name_normalized(self.db, user_id, normalized_name) is not None
            ):
                raise DuplicateCompanyNameError(
                    f"A company named '{company_in.name}' already exists."
                ) from exc
            raise
        except Exception:
            self.db.rollback()
            raise

    def get_company(
        self,
        company_id: UUID,
        user_id: UUID,
    ) -> Company:
        """Retrieve a company by its ID, ensuring it belongs to the authenticated user.

        Treats a company belonging to another user as not found to prevent
        account enumeration and IDOR disclosures.

        Args:
            company_id: The UUID primary key of the company.
            user_id: The UUID primary key of the authenticated user.

        Returns:
            The matching Company instance.

        Raises:
            CompanyNotFoundError: If the company does not exist or is not owned by user_id.
        """
        try:
            company = company_repository.get_by_id(
                db=self.db,
                company_id=company_id,
                user_id=user_id,
            )
        except Exception:
            self.db.rollback()
            raise

        if company is None:
            raise CompanyNotFoundError(f"Company '{company_id}' not found.")
        return company

    def list_companies(
        self,
        user_id: UUID,
        is_archived: bool | None = False,
    ) -> list[Company]:
        """List companies belonging to the authenticated user with optional archive filtering.

        Preserves alphabetical ordering by name ascending. Never exposes another
        user's records.

        Args:
            user_id: The UUID primary key of the authenticated user.
            is_archived: Optional boolean filter:
                - False (default): return active companies only.
                - True: return archived companies only.
                - None: return all companies (both active and archived).

        Returns:
            A list of Company instances ordered by name ascending.
        """
        try:
            return company_repository.list_by_user(
                db=self.db,
                user_id=user_id,
                is_archived=is_archived,
            )
        except Exception:
            self.db.rollback()
            raise

    def update_company(
        self,
        company_id: UUID,
        user_id: UUID,
        company_in: CompanyUpdate | None = None,
        **kwargs: Any,
    ) -> Company:
        """Update an existing company owned by the authenticated user.

        Applies only fields explicitly provided by the client (via model_dump(exclude_unset=True)).
        Rejects empty update payloads. Ensures changing company name does not collide
        with another company owned by the same user. Preserves nullable-field clearing
        behavior (e.g. setting website=None).

        Commits on success; rolls back on failure.

        Args:
            company_id: The UUID primary key of the company to update.
            user_id: The UUID primary key of the authenticated user.
            company_in: Optional CompanyUpdate Pydantic schema instance.
            **kwargs: Field arguments used to construct CompanyUpdate if company_in is omitted.

        Returns:
            The updated and committed Company instance.

        Raises:
            CompanyNotFoundError: If the company does not exist or is not owned by user_id.
            InvalidCompanyUpdateError: If no fields were provided for update.
            DuplicateCompanyNameError: If the updated name duplicates another owned company.
            pydantic.ValidationError: If field inputs fail schema validation.
            CompanyServiceError: On other database or service failures.
        """
        if company_in is None:
            company_in = CompanyUpdate(**kwargs)

        # Ensure the company exists and belongs to the requesting user
        existing_company = self.get_company(company_id=company_id, user_id=user_id)

        update_data = company_in.model_dump(exclude_unset=True)
        if not update_data:
            raise InvalidCompanyUpdateError("At least one field must be provided for update.")

        # If name is being updated, check for collision with other companies owned by this user
        if "name" in update_data:
            new_name = update_data["name"]
            new_normalized = new_name.strip().lower()
            if new_normalized != existing_company.name_normalized:
                try:
                    duplicate = company_repository.get_by_name_normalized(
                        db=self.db,
                        user_id=user_id,
                        name_normalized=new_normalized,
                    )
                except Exception:
                    self.db.rollback()
                    raise

                if duplicate is not None and duplicate.id != company_id:
                    raise DuplicateCompanyNameError(
                        f"A company named '{new_name}' already exists."
                    )

        try:
            updated_company = company_repository.update(
                db=self.db,
                company_id=company_id,
                user_id=user_id,
                update_data=update_data,
            )
            if updated_company is None:
                raise CompanyNotFoundError(f"Company '{company_id}' not found.")
            self.db.commit()
            self.db.refresh(updated_company)
            return updated_company
        except IntegrityError as exc:
            self.db.rollback()
            if "uq_companies_user_name_normalized" in str(exc).lower():
                raise DuplicateCompanyNameError(
                    f"A company named '{update_data.get('name')}' already exists."
                ) from exc
            raise
        except Exception:
            self.db.rollback()
            raise

    def archive_company(
        self,
        company_id: UUID,
        user_id: UUID,
    ) -> Company:
        """Archive a company owned by the authenticated user.

        Preserves the record in the database by setting is_archived to True.
        Safe and idempotent if the company is already archived.

        Commits on success; rolls back on failure.

        Args:
            company_id: The UUID primary key of the company to archive.
            user_id: The UUID primary key of the authenticated user.

        Returns:
            The archived and committed Company instance.

        Raises:
            CompanyNotFoundError: If the company does not exist or is not owned by user_id.
            CompanyServiceError: On other database or service failures.
        """
        # Ensure company exists and belongs to requesting user
        existing_company = self.get_company(company_id=company_id, user_id=user_id)

        if existing_company.is_archived:
            return existing_company

        try:
            archived = company_repository.archive(
                db=self.db,
                company_id=company_id,
                user_id=user_id,
            )
            if archived is None:
                raise CompanyNotFoundError(f"Company '{company_id}' not found.")
            self.db.commit()
            self.db.refresh(archived)
            return archived
        except Exception:
            self.db.rollback()
            raise

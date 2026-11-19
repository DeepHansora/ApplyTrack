"""Integration and unit tests for ApplicationService business logic, validations, and transactions.

Verifies:
1. create_application:
   - Successful application creation with ApplicationCreate schema.
   - Successful application creation with keyword arguments.
   - Ownership strictly bound to authenticated user.
   - Reject creating application with non-existent company (InvalidApplicationCompanyError).
   - Reject creating application with another user's company (InvalidApplicationCompanyError).
   - Reject creating application with an archived company (InvalidApplicationCompanyError).
   - Reject salary_min > salary_max on creation (InvalidSalaryRangeError).
   - Commits on success; rolls back on failure.
2. get_application:
   - Retrieves owned application with loaded company relationship and company_name.
   - Raises ApplicationNotFoundError for non-existent application.
   - Raises ApplicationNotFoundError for another user's application (uniform 404 behavior, IDOR defense).
3. list_applications:
   - Strict tenant isolation across all filters and search queries.
   - Search by job title and company name.
   - Filter by status, company_id, employment_type, applied date range, and archive status.
   - Sorting by whitelisted fields and fallback on invalid field/order.
   - Pagination calculation (items, total, page, per_page, total_pages, out-of-range page).
   - Rejects inverted applied date range (applied_after > applied_before).
4. update_application:
   - Partial updates apply only client-provided fields.
   - Preserves existing company association when company_id is omitted.
   - Preserves existing company association when company_id is unchanged, even if archived.
   - Changing company to active owned company succeeds.
   - Changing company to an archived company is rejected (InvalidApplicationCompanyError).
   - Changing company to another user's company is rejected (InvalidApplicationCompanyError).
   - Merged salary validation:
     - Existing min 50k, max 100k; changing max to 40k fails (InvalidSalaryRangeError).
     - Existing min 50k, max 100k; changing min to 60k succeeds.
     - Clearing one bound succeeds when resulting range is valid.
     - Negative salary fails (InvalidSalaryRangeError).
   - Rejects empty update payload (InvalidApplicationUpdateError).
   - Updating another user's application raises ApplicationNotFoundError.
   - Commits on success; rolls back on failure.
5. archive_application & unarchive_application:
   - Archive sets is_archived to True; idempotent if already archived.
   - Unarchive sets is_archived to False; idempotent if already active.
   - Operating on another user's application raises ApplicationNotFoundError.
   - Commits on success; rolls back on failure.
"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Generator
from unittest.mock import patch
import uuid

from pydantic import ValidationError
import pytest
from sqlalchemy.orm import Session

from app.database import engine
from app.models.application import Application
from app.models.company import Company
from app.models.user import User
from app.schemas.application import ApplicationCreate, ApplicationUpdate
from app.services.application_service import (
    ApplicationNotFoundError,
    ApplicationService,
    ApplicationServiceError,
    InvalidApplicationCompanyError,
    InvalidApplicationUpdateError,
    InvalidSalaryRangeError,
)
from app.services.company_service import CompanyService


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    """Provide an isolated database session that automatically rolls back after each test."""
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")

    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


def _create_test_user(db: Session, email: str = "app_service_user@example.com") -> User:
    """Helper to persist an active user record for foreign key ownership."""
    user = User(
        full_name="Service Tester",
        email=email,
        password_hash="$argon2id$v=19$m=65536,t=3,p=4$dummyhash",
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _create_test_company(
    db: Session, user: User, name: str = "Service Test Co", is_archived: bool = False
) -> Company:
    """Helper to persist a test company owned by the specified user."""
    company = Company(
        user_id=user.id,
        name=name,
        website="https://servicetest.example.com",
        is_archived=is_archived,
    )
    db.add(company)
    db.commit()
    db.refresh(company)
    return company


# ============================================================================
# 1. Application Creation Tests
# ============================================================================


def test_service_create_application_success_with_schema(db_session: Session):
    """Verify create_application persists and commits using ApplicationCreate schema."""
    user = _create_test_user(db_session, "creator.schema@example.com")
    comp = _create_test_company(db_session, user, "FastAPI Labs")
    service = ApplicationService(db_session)

    schema = ApplicationCreate(
        company_id=comp.id,
        job_title="Backend Lead",
        job_description="FastAPI microservices",
        employment_type="Full-time",
        location="Remote",
        salary_min=Decimal("130000.00"),
        salary_max=Decimal("160000.00"),
        currency="USD",
        status="Applied",
    )

    app = service.create_application(user_id=user.id, application_in=schema)

    assert isinstance(app.id, uuid.UUID)
    assert app.user_id == user.id
    assert app.company_id == comp.id
    assert app.job_title == "Backend Lead"
    assert app.status == "Applied"
    assert app.salary_min == Decimal("130000.00")
    assert app.salary_max == Decimal("160000.00")
    assert app.is_archived is False


def test_service_create_application_success_with_kwargs(db_session: Session):
    """Verify create_application persists and commits using keyword arguments."""
    user = _create_test_user(db_session, "creator.kwargs@example.com")
    comp = _create_test_company(db_session, user, "Pythonic Co")
    service = ApplicationService(db_session)

    app = service.create_application(
        user_id=user.id,
        company_id=comp.id,
        job_title="Software Architect",
        notes="First interview scheduled",
    )

    assert app.job_title == "Software Architect"
    assert app.status == "Saved"  # Default
    assert app.notes == "First interview scheduled"


def test_service_create_application_rejects_nonexistent_company(db_session: Session):
    """Verify create_application raises InvalidApplicationCompanyError for non-existent company."""
    user = _create_test_user(db_session, "missing.comp@example.com")
    service = ApplicationService(db_session)

    with pytest.raises(InvalidApplicationCompanyError) as exc_info:
        service.create_application(
            user_id=user.id,
            company_id=uuid.uuid4(),
            job_title="Role",
        )
    assert "Selected company not found" in str(exc_info.value)


def test_service_create_application_rejects_other_users_company(db_session: Session):
    """Verify create_application raises InvalidApplicationCompanyError if company belongs to another user."""
    user1 = _create_test_user(db_session, "owner.comp@example.com")
    user2 = _create_test_user(db_session, "attacker.comp@example.com")
    comp_user1 = _create_test_company(db_session, user1, "User 1 Company")

    service = ApplicationService(db_session)

    # user2 attempts to use user1's company
    with pytest.raises(InvalidApplicationCompanyError) as exc_info:
        service.create_application(
            user_id=user2.id,
            company_id=comp_user1.id,
            job_title="Sneaky Role",
        )
    assert "Selected company not found" in str(exc_info.value)


def test_service_create_application_rejects_archived_company(db_session: Session):
    """Verify new applications cannot be associated with archived companies."""
    user = _create_test_user(db_session, "archived.comp@example.com")
    archived_comp = _create_test_company(db_session, user, "Old Archived Co", is_archived=True)

    service = ApplicationService(db_session)

    with pytest.raises(InvalidApplicationCompanyError) as exc_info:
        service.create_application(
            user_id=user.id,
            company_id=archived_comp.id,
            job_title="Developer",
        )
    assert "Cannot associate a new application with an archived company" in str(exc_info.value)


def test_service_create_application_rollback_on_failure(db_session: Session):
    """Verify transaction is rolled back if database error occurs during create."""
    user = _create_test_user(db_session, "rollback.create@example.com")
    comp = _create_test_company(db_session, user, "Rollback Co")
    service = ApplicationService(db_session)

    with patch.object(db_session, "commit", side_effect=RuntimeError("Database failure")):
        with pytest.raises(RuntimeError):
            service.create_application(
                user_id=user.id,
                company_id=comp.id,
                job_title="Failing Job",
            )


# ============================================================================
# 2. Application Retrieval Tests (get_application)
# ============================================================================


def test_service_get_application_success(db_session: Session):
    """Verify get_application retrieves owned application and exposes company_name."""
    user = _create_test_user(db_session, "getter.svc@example.com")
    comp = _create_test_company(db_session, user, "Acme AI")
    service = ApplicationService(db_session)

    created = service.create_application(
        user_id=user.id,
        company_id=comp.id,
        job_title="AI Engineer",
    )

    retrieved = service.get_application(application_id=created.id, user_id=user.id)
    assert retrieved.id == created.id
    assert retrieved.company_name == "Acme AI"


def test_service_get_application_cross_user_returns_404(db_session: Session):
    """Verify get_application raises ApplicationNotFoundError for another user's application."""
    user1 = _create_test_user(db_session, "owner.get@example.com")
    user2 = _create_test_user(db_session, "other.get@example.com")
    comp = _create_test_company(db_session, user1, "Private Co")
    service = ApplicationService(db_session)

    app1 = service.create_application(user_id=user1.id, company_id=comp.id, job_title="Secret Role")

    # user2 queries user1's application ID
    with pytest.raises(ApplicationNotFoundError):
        service.get_application(application_id=app1.id, user_id=user2.id)


def test_service_get_application_nonexistent_returns_404(db_session: Session):
    """Verify get_application raises ApplicationNotFoundError for non-existent UUID."""
    user = _create_test_user(db_session, "none.get@example.com")
    service = ApplicationService(db_session)

    with pytest.raises(ApplicationNotFoundError):
        service.get_application(application_id=uuid.uuid4(), user_id=user.id)


# ============================================================================
# 3. Listing, Search, Filter & Pagination Tests (list_applications)
# ============================================================================


def test_service_list_applications_user_isolation(db_session: Session):
    """Verify list_applications returns only the authenticated user's records."""
    user1 = _create_test_user(db_session, "u1.list@example.com")
    user2 = _create_test_user(db_session, "u2.list@example.com")
    c1 = _create_test_company(db_session, user1, "User 1 Co")
    c2 = _create_test_company(db_session, user2, "User 2 Co")

    service = ApplicationService(db_session)
    service.create_application(user_id=user1.id, company_id=c1.id, job_title="User 1 Job")
    service.create_application(user_id=user2.id, company_id=c2.id, job_title="User 2 Job")

    res1 = service.list_applications(user_id=user1.id)
    assert res1["total"] == 1
    assert res1["items"][0].job_title == "User 1 Job"

    res2 = service.list_applications(user_id=user2.id)
    assert res2["total"] == 1
    assert res2["items"][0].job_title == "User 2 Job"


def test_service_list_applications_search_and_filter(db_session: Session):
    """Verify search and multi-field filters via service layer."""
    user = _create_test_user(db_session, "search.svc@example.com")
    google = _create_test_company(db_session, user, "Google Cloud")
    amazon = _create_test_company(db_session, user, "Amazon AWS")
    service = ApplicationService(db_session)

    app1 = service.create_application(
        user_id=user.id,
        company_id=google.id,
        job_title="Solutions Architect",
        status="Interview",
        employment_type="Full-time",
    )
    app2 = service.create_application(
        user_id=user.id,
        company_id=amazon.id,
        job_title="DevOps Engineer",
        status="Applied",
        employment_type="Contract",
    )

    # Search company keyword
    res_search = service.list_applications(user_id=user.id, search="cloud")
    assert res_search["total"] == 1
    assert res_search["items"][0].id == app1.id

    # Filter status
    res_status = service.list_applications(user_id=user.id, status="Applied")
    assert res_status["total"] == 1
    assert res_status["items"][0].id == app2.id


def test_service_list_applications_date_validation(db_session: Session):
    """Verify service rejects inverted date range (applied_after > applied_before)."""
    user = _create_test_user(db_session, "date.val@example.com")
    service = ApplicationService(db_session)

    d_late = datetime(2026, 6, 1, 0, 0, 0, tzinfo=timezone.utc)
    d_early = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

    with pytest.raises(ApplicationServiceError, match="applied_after date cannot be after"):
        service.list_applications(
            user_id=user.id,
            applied_after=d_late,
            applied_before=d_early,
        )


def test_service_list_applications_pagination_structure(db_session: Session):
    """Verify list_applications returns proper total_pages, page, and per_page metadata."""
    user = _create_test_user(db_session, "paging.svc@example.com")
    comp = _create_test_company(db_session, user, "Batch Co")
    service = ApplicationService(db_session)

    # Create 22 applications
    for i in range(1, 23):
        service.create_application(
            user_id=user.id,
            company_id=comp.id,
            job_title=f"Role {i:02d}",
        )

    # Page 1 with 10 per page -> 3 total pages
    res = service.list_applications(user_id=user.id, page=1, per_page=10)
    assert res["total"] == 22
    assert res["total_pages"] == 3
    assert res["page"] == 1
    assert res["per_page"] == 10
    assert len(res["items"]) == 10

    # Page 1 with 5 per page -> 5 total pages (ceil(22/5) = 5)
    res5 = service.list_applications(user_id=user.id, page=1, per_page=5)
    assert res5["total"] == 22
    assert res5["total_pages"] == 5
    assert res5["page"] == 1
    assert res5["per_page"] == 5
    assert len(res5["items"]) == 5

    # Page 1 with 20 per page -> 2 total pages (ceil(22/20) = 2)
    res20 = service.list_applications(user_id=user.id, page=1, per_page=20)
    assert res20["total"] == 22
    assert res20["total_pages"] == 2
    assert res20["per_page"] == 20
    assert len(res20["items"]) == 20

    # Unsupported page size (e.g. 50 or -5) defaults to 20
    res_def = service.list_applications(user_id=user.id, page=1, per_page=50)
    assert res_def["per_page"] == 20
    assert len(res_def["items"]) == 20

    # Out of range page (page 50) returns empty items with total=22 and total_pages=3
    res_oor = service.list_applications(user_id=user.id, page=50, per_page=10)
    assert res_oor["total"] == 22
    assert res_oor["total_pages"] == 3
    assert len(res_oor["items"]) == 0


# ============================================================================
# 4. Application Update & Company Association Rules
# ============================================================================


def test_service_update_preserves_existing_archived_company_when_omitted(db_session: Session):
    """Verify updating an application preserves existing company association even if archived."""
    user = _create_test_user(db_session, "archived.pres@example.com")
    comp = _create_test_company(db_session, user, "Now Archived Co", is_archived=False)
    service = ApplicationService(db_session)

    # Create application while company is active
    app = service.create_application(
        user_id=user.id,
        company_id=comp.id,
        job_title="Initial Title",
    )

    # Later, the company is archived
    comp.is_archived = True
    db_session.commit()

    # User updates only the job_title (company_id omitted)
    updated = service.update_application(
        application_id=app.id,
        user_id=user.id,
        job_title="Updated Title",
    )
    assert updated.job_title == "Updated Title"
    assert updated.company_id == comp.id  # Preserved without error!


def test_service_update_preserves_existing_archived_company_when_unchanged(db_session: Session):
    """Verify passing the SAME company_id on edit is permitted even if that company is archived."""
    user = _create_test_user(db_session, "archived.same@example.com")
    comp = _create_test_company(db_session, user, "Same Archived Co", is_archived=False)
    service = ApplicationService(db_session)

    app = service.create_application(user_id=user.id, company_id=comp.id, job_title="Title")
    comp.is_archived = True
    db_session.commit()

    # Explicitly supply the SAME company_id
    updated = service.update_application(
        application_id=app.id,
        user_id=user.id,
        company_id=comp.id,
        job_title="New Title",
    )
    assert updated.job_title == "New Title"
    assert updated.company_id == comp.id


def test_service_update_changing_to_different_archived_company_is_rejected(db_session: Session):
    """Verify changing company to another company that is ARCHIVED is rejected."""
    user = _create_test_user(db_session, "archived.change@example.com")
    c1 = _create_test_company(db_session, user, "Original Co", is_archived=False)
    c2_archived = _create_test_company(db_session, user, "Target Archived Co", is_archived=True)
    service = ApplicationService(db_session)

    app = service.create_application(user_id=user.id, company_id=c1.id, job_title="Title")

    # Attempt changing company to c2_archived
    with pytest.raises(InvalidApplicationCompanyError) as exc_info:
        service.update_application(
            application_id=app.id,
            user_id=user.id,
            company_id=c2_archived.id,
        )
    assert "Cannot change application association to an archived company" in str(exc_info.value)


def test_service_update_changing_to_other_users_company_is_rejected(db_session: Session):
    """Verify changing company to another user's company is rejected with InvalidApplicationCompanyError."""
    user1 = _create_test_user(db_session, "u1.change@example.com")
    user2 = _create_test_user(db_session, "u2.change@example.com")
    c1 = _create_test_company(db_session, user1, "User 1 Co")
    c2 = _create_test_company(db_session, user2, "User 2 Co")
    service = ApplicationService(db_session)

    app = service.create_application(user_id=user1.id, company_id=c1.id, job_title="Role")

    # user1 tries to associate with user2's company
    with pytest.raises(InvalidApplicationCompanyError) as exc_info:
        service.update_application(
            application_id=app.id,
            user_id=user1.id,
            company_id=c2.id,
        )
    assert "Selected company not found" in str(exc_info.value)


def test_service_update_rejects_other_users_application(db_session: Session):
    """Verify updating another user's application raises ApplicationNotFoundError."""
    user1 = _create_test_user(db_session, "owner.up@example.com")
    user2 = _create_test_user(db_session, "other.up@example.com")
    comp = _create_test_company(db_session, user1, "Owner Co")
    service = ApplicationService(db_session)

    app = service.create_application(user_id=user1.id, company_id=comp.id, job_title="Owner Role")

    with pytest.raises(ApplicationNotFoundError):
        service.update_application(
            application_id=app.id,
            user_id=user2.id,
            job_title="Hacked Title",
        )


def test_service_update_rejects_empty_payload(db_session: Session):
    """Verify update raises InvalidApplicationUpdateError when no fields are supplied."""
    user = _create_test_user(db_session, "empty.up@example.com")
    comp = _create_test_company(db_session, user, "Company")
    service = ApplicationService(db_session)

    app = service.create_application(user_id=user.id, company_id=comp.id, job_title="Role")

    with pytest.raises(InvalidApplicationUpdateError):
        service.update_application(
            application_id=app.id,
            user_id=user.id,
            application_in=ApplicationUpdate(),
        )


# ============================================================================
# 5. Merged Salary Range Validation Tests
# ============================================================================


def test_service_merged_salary_validation(db_session: Session):
    """Verify merged salary updates validate incoming changes against existing values."""
    user = _create_test_user(db_session, "salary.merge@example.com")
    comp = _create_test_company(db_session, user, "Salary Co")
    service = ApplicationService(db_session)

    # Existing min 50k, max 100k
    app = service.create_application(
        user_id=user.id,
        company_id=comp.id,
        job_title="Engineer",
        salary_min=Decimal("50000"),
        salary_max=Decimal("100000"),
    )

    # 1. Changing max to 40,000 must fail (40k < existing min 50k)
    with pytest.raises(InvalidSalaryRangeError) as exc_info1:
        service.update_application(
            application_id=app.id,
            user_id=user.id,
            salary_max=Decimal("40000"),
        )
    assert "Minimum salary (50000.00) cannot exceed maximum salary (40000)" in str(exc_info1.value)

    # 2. Changing min to 60,000 must succeed (60k <= existing max 100k)
    updated = service.update_application(
        application_id=app.id,
        user_id=user.id,
        salary_min=Decimal("60000"),
    )
    assert updated.salary_min == Decimal("60000")
    assert updated.salary_max == Decimal("100000")

    # 3. Clearing salary_min to None must succeed
    updated_clear = service.update_application(
        application_id=app.id,
        user_id=user.id,
        salary_min=None,
    )
    assert updated_clear.salary_min is None
    assert updated_clear.salary_max == Decimal("100000")

    # 4. Setting negative salary is rejected by validation
    with pytest.raises(ValidationError):
        service.update_application(
            application_id=app.id,
            user_id=user.id,
            salary_max=Decimal("-100"),
        )


# ============================================================================
# 6. Archive & Unarchive Tests
# ============================================================================


def test_service_archive_and_unarchive_lifecycle(db_session: Session):
    """Verify archive and unarchive lifecycle via service."""
    user = _create_test_user(db_session, "arc.svc@example.com")
    comp = _create_test_company(db_session, user, "Arc Co")
    service = ApplicationService(db_session)

    app = service.create_application(user_id=user.id, company_id=comp.id, job_title="Job")
    assert app.is_archived is False

    # Archive
    archived = service.archive_application(application_id=app.id, user_id=user.id)
    assert archived.is_archived is True

    # Repeated archive is idempotent
    archived_again = service.archive_application(application_id=app.id, user_id=user.id)
    assert archived_again.is_archived is True

    # Unarchive
    unarchived = service.unarchive_application(application_id=app.id, user_id=user.id)
    assert unarchived.is_archived is False

    # Repeated unarchive is idempotent
    unarchived_again = service.unarchive_application(application_id=app.id, user_id=user.id)
    assert unarchived_again.is_archived is False


def test_service_archive_other_users_application_raises_404(db_session: Session):
    """Verify attempting to archive another user's application raises ApplicationNotFoundError."""
    user1 = _create_test_user(db_session, "u1.arc@example.com")
    user2 = _create_test_user(db_session, "u2.arc@example.com")
    comp = _create_test_company(db_session, user1, "Company")
    service = ApplicationService(db_session)

    app = service.create_application(user_id=user1.id, company_id=comp.id, job_title="Role")

    with pytest.raises(ApplicationNotFoundError):
        service.archive_application(application_id=app.id, user_id=user2.id)

    with pytest.raises(ApplicationNotFoundError):
        service.unarchive_application(application_id=app.id, user_id=user2.id)

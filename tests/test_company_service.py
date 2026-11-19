"""Comprehensive integration and unit tests for CompanyService business logic and transactions.

Verifies:
1. create_company:
   - Successful company creation using CompanyCreate Pydantic schema.
   - Successful company creation using keyword arguments.
   - Binds ownership strictly to authenticated user_id.
   - Normalizes company name and auto-computes name_normalized.
   - Rejects duplicate company name for same user with DuplicateCompanyNameError.
   - Allows different users to own companies with the exact same name.
   - Handles concurrent database race conditions with DuplicateCompanyNameError.
   - Rolls back transaction on unexpected database failure.
2. get_company:
   - Retrieves an owned company by UUID.
   - Raises CompanyNotFoundError for non-existent company UUID.
   - Raises CompanyNotFoundError for another user's company (uniform 404 behavior, IDOR defense).
3. list_companies:
   - Returns only the requesting user's companies (strict user isolation).
   - Preserves alphabetical ordering by company name ascending (A-Z).
   - Filters active companies only (is_archived=False, default).
   - Filters archived companies only (is_archived=True).
   - Returns all companies when is_archived=None.
4. update_company:
   - Partial updates apply only client-provided fields.
   - Explicitly clearing optional fields (website=None) sets database field to None.
   - Rejects empty update payloads with InvalidCompanyUpdateError.
   - Rejects explicit null company name (name=None) via validation error.
   - Rejects updates that would collide with another owned company name.
   - Allows updating other fields while preserving existing company name.
   - Rejects updating another user's company with CompanyNotFoundError.
   - Rolls back transaction on database failure.
5. archive_company:
   - Successfully archives owned company and commits to database.
   - Preserves company record in database without permanent deletion.
   - Re-archiving an already archived company is idempotent and safe.
   - Rejects archiving another user's company with CompanyNotFoundError.
   - Rolls back transaction on database failure.
"""

from typing import Generator
from unittest.mock import patch
import uuid

from pydantic import ValidationError
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.database import engine
from app.models.company import Company
from app.models.user import User
from app.schemas.company import CompanyCreate, CompanyUpdate
from app.services.company_service import (
    CompanyNotFoundError,
    CompanyService,
    CompanyServiceError,
    DuplicateCompanyNameError,
    InvalidCompanyUpdateError,
)


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


def _create_test_user(db: Session, email: str = "service_test_user@example.com") -> User:
    """Helper to persist an active test user for ownership foreign keys."""
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


# ============================================================================
# 1. Company Creation Tests
# ============================================================================


def test_service_create_company_success_with_schema(db_session: Session):
    """Verify create_company persists company and commits using CompanyCreate schema."""
    user = _create_test_user(db_session, "creator_schema@example.com")
    service = CompanyService(db_session)

    schema = CompanyCreate(
        name="  Stripe  ",
        website="https://stripe.com",
        industry="Financial Infrastructure",
        location="South San Francisco, CA",
        notes="Applied for senior engineer role",
    )

    company = service.create_company(user.id, company_in=schema)

    assert isinstance(company.id, uuid.UUID)
    assert company.user_id == user.id
    assert company.name == "Stripe"
    assert company.name_normalized == "stripe"
    assert company.website == "https://stripe.com"
    assert company.industry == "Financial Infrastructure"
    assert company.is_archived is False


def test_service_create_company_success_with_kwargs(db_session: Session):
    """Verify create_company accepts kwargs and validates via CompanyCreate."""
    user = _create_test_user(db_session, "creator_kwargs@example.com")
    service = CompanyService(db_session)

    company = service.create_company(
        user.id,
        name="Google",
        location="Mountain View, CA",
    )

    assert company.name == "Google"
    assert company.name_normalized == "google"
    assert company.location == "Mountain View, CA"
    assert company.website is None


def test_service_create_company_duplicate_name_raises_error(db_session: Session):
    """Verify duplicate company name for the same user raises DuplicateCompanyNameError."""
    user = _create_test_user(db_session, "dupe_user@example.com")
    service = CompanyService(db_session)

    service.create_company(user.id, name="Netflix")

    # Attempt to create with different casing and whitespace
    with pytest.raises(DuplicateCompanyNameError) as exc_info:
        service.create_company(user.id, name="  NETFLIX  ")

    assert "A company named 'NETFLIX' already exists" in str(exc_info.value)


def test_service_create_company_different_users_same_name_allowed(db_session: Session):
    """Verify different users can create companies with the exact same name."""
    user1 = _create_test_user(db_session, "user_apple1@example.com")
    user2 = _create_test_user(db_session, "user_apple2@example.com")
    service = CompanyService(db_session)

    c1 = service.create_company(user1.id, name="Apple")
    c2 = service.create_company(user2.id, name="Apple")

    assert c1.id != c2.id
    assert c1.name == "Apple"
    assert c2.name == "Apple"
    assert c1.user_id == user1.id
    assert c2.user_id == user2.id


def test_service_create_company_concurrent_race_integrity_error_handled(db_session: Session):
    """Verify concurrent creation race triggering IntegrityError raises DuplicateCompanyNameError."""
    user = _create_test_user(db_session, "race_creator@example.com")
    service = CompanyService(db_session)

    # Mock company_repository.create to simulate an IntegrityError from a concurrent insert race
    with patch("app.repositories.company_repository.create") as mock_create:
        mock_create.side_effect = IntegrityError(
            "duplicate key value violates unique constraint uq_companies_user_name_normalized",
            params={},
            orig=Exception("uq_companies_user_name_normalized"),
        )
        with pytest.raises(DuplicateCompanyNameError) as exc_info:
            service.create_company(user.id, name="Datadog")

        assert "A company named 'Datadog' already exists" in str(exc_info.value)


def test_service_create_company_rollback_on_database_error(db_session: Session):
    """Verify create_company rolls back the transaction when an unexpected error occurs."""
    user = _create_test_user(db_session, "err_rollback@example.com")
    service = CompanyService(db_session)

    with patch.object(db_session, "rollback") as mock_rollback:
        with patch("app.repositories.company_repository.create", side_effect=OperationalError("DB down", {}, None)):
            with pytest.raises(OperationalError):
                service.create_company(user.id, name="Failing Corp")

        mock_rollback.assert_called_once()


# ============================================================================
# 2. Company Retrieval Tests
# ============================================================================


def test_service_get_company_success(db_session: Session):
    """Verify get_company retrieves the matching company for the owning user."""
    user = _create_test_user(db_session, "getter@example.com")
    service = CompanyService(db_session)
    created = service.create_company(user.id, name="Linear")

    retrieved = service.get_company(company_id=created.id, user_id=user.id)
    assert retrieved.id == created.id
    assert retrieved.name == "Linear"


def test_service_get_company_non_existent_raises_not_found(db_session: Session):
    """Verify get_company raises CompanyNotFoundError for random non-existent UUID."""
    user = _create_test_user(db_session, "non_existent_user@example.com")
    service = CompanyService(db_session)
    missing_id = uuid.uuid4()

    with pytest.raises(CompanyNotFoundError) as exc_info:
        service.get_company(company_id=missing_id, user_id=user.id)

    assert f"Company '{missing_id}' not found" in str(exc_info.value)


def test_service_get_company_another_user_raises_not_found(db_session: Session):
    """Verify get_company raises CompanyNotFoundError for another user's company (IDOR defense)."""
    user1 = _create_test_user(db_session, "owner_idor@example.com")
    user2 = _create_test_user(db_session, "attacker_idor@example.com")
    service = CompanyService(db_session)

    company1 = service.create_company(user1.id, name="Confidential Inc")

    with pytest.raises(CompanyNotFoundError) as exc_info:
        service.get_company(company_id=company1.id, user_id=user2.id)

    # Must return generic not found without confirming existence
    assert f"Company '{company1.id}' not found" in str(exc_info.value)


# ============================================================================
# 3. Company Listing Tests
# ============================================================================


def test_service_list_companies_user_isolation_and_alphabetical_order(db_session: Session):
    """Verify list_companies returns only requesting user's companies in A-Z order."""
    user_a = _create_test_user(db_session, "user_a@example.com")
    user_b = _create_test_user(db_session, "user_b@example.com")
    service = CompanyService(db_session)

    # User A companies (created out of order)
    service.create_company(user_a.id, name="Zeta Corp")
    service.create_company(user_a.id, name="Alpha Labs")
    service.create_company(user_a.id, name="Beta Dynamics")

    # User B company
    service.create_company(user_b.id, name="Delta Systems")

    # List for User A
    a_companies = service.list_companies(user_a.id)
    assert [c.name for c in a_companies] == ["Alpha Labs", "Beta Dynamics", "Zeta Corp"]

    # List for User B
    b_companies = service.list_companies(user_b.id)
    assert [c.name for c in b_companies] == ["Delta Systems"]


def test_service_list_companies_archived_filtering(db_session: Session):
    """Verify list_companies supports active and archived filtering."""
    user = _create_test_user(db_session, "archive_filter@example.com")
    service = CompanyService(db_session)

    active = service.create_company(user.id, name="Active Corp")
    archived = service.create_company(user.id, name="Archived Corp")
    service.archive_company(archived.id, user.id)

    # 1. Default (active only, is_archived=False)
    active_only = service.list_companies(user.id, is_archived=False)
    assert len(active_only) == 1
    assert active_only[0].id == active.id

    # 2. Archived only (is_archived=True)
    archived_only = service.list_companies(user.id, is_archived=True)
    assert len(archived_only) == 1
    assert archived_only[0].id == archived.id

    # 3. Unfiltered (is_archived=None)
    all_companies = service.list_companies(user.id, is_archived=None)
    assert len(all_companies) == 2


# ============================================================================
# 4. Company Update Tests
# ============================================================================


def test_service_update_company_partial_success(db_session: Session):
    """Verify update_company applies only provided fields and commits."""
    user = _create_test_user(db_session, "updater_service@example.com")
    service = CompanyService(db_session)

    comp = service.create_company(
        user.id,
        name="Notion",
        website="https://notion.so",
        location="San Francisco, CA",
    )

    update_in = CompanyUpdate(location="New York, NY")
    updated = service.update_company(comp.id, user.id, company_in=update_in)

    assert updated.location == "New York, NY"
    assert updated.name == "Notion"
    assert updated.website == "https://notion.so"


def test_service_update_company_nullable_field_clearing(db_session: Session):
    """Verify update_company allows explicitly clearing optional fields to None."""
    user = _create_test_user(db_session, "clearing_service@example.com")
    service = CompanyService(db_session)

    comp = service.create_company(
        user.id,
        name="Figma",
        website="https://figma.com",
    )
    assert comp.website == "https://figma.com"

    # Explicitly clear website to None
    updated = service.update_company(comp.id, user.id, website=None)
    assert updated.website is None


def test_service_update_company_rejects_empty_payload(db_session: Session):
    """Verify update_company raises InvalidCompanyUpdateError when no fields are supplied."""
    user = _create_test_user(db_session, "empty_update_user@example.com")
    service = CompanyService(db_session)
    comp = service.create_company(user.id, name="Test Empty")

    with pytest.raises(InvalidCompanyUpdateError) as exc_info:
        service.update_company(comp.id, user.id, company_in=CompanyUpdate())

    assert "At least one field must be provided for update" in str(exc_info.value)


def test_service_update_company_rejects_explicit_null_name(db_session: Session):
    """Verify passing name=None to update_company raises validation error."""
    user = _create_test_user(db_session, "null_name_update@example.com")
    service = CompanyService(db_session)
    comp = service.create_company(user.id, name="Test Null")

    with pytest.raises(ValidationError):
        service.update_company(comp.id, user.id, name=None)


def test_service_update_company_duplicate_name_raises_error(db_session: Session):
    """Verify updating company name to match an existing owned company raises error."""
    user = _create_test_user(db_session, "dupe_updater@example.com")
    service = CompanyService(db_session)

    c1 = service.create_company(user.id, name="Company One")
    c2 = service.create_company(user.id, name="Company Two")

    # Attempt to rename c2 to "company one" (case-insensitive collision)
    with pytest.raises(DuplicateCompanyNameError) as exc_info:
        service.update_company(c2.id, user.id, name="  COMPANY ONE  ")

    assert "A company named 'COMPANY ONE' already exists" in str(exc_info.value)


def test_service_update_company_same_name_no_error(db_session: Session):
    """Verify updating a company with its current name does not trigger duplicate error."""
    user = _create_test_user(db_session, "self_rename@example.com")
    service = CompanyService(db_session)

    comp = service.create_company(user.id, name="GitHub", location="San Francisco")

    # Update with the same name and a new location
    updated = service.update_company(comp.id, user.id, name="GitHub", location="Remote")
    assert updated.name == "GitHub"
    assert updated.location == "Remote"


def test_service_update_company_another_user_raises_not_found(db_session: Session):
    """Verify update_company raises CompanyNotFoundError for another user's company."""
    user1 = _create_test_user(db_session, "victim_update@example.com")
    user2 = _create_test_user(db_session, "attacker_update@example.com")
    service = CompanyService(db_session)

    comp = service.create_company(user1.id, name="Secure Co")

    with pytest.raises(CompanyNotFoundError):
        service.update_company(comp.id, user2.id, location="Compromised")

    db_session.refresh(comp)
    assert comp.location is None


def test_service_update_company_rollback_on_database_error(db_session: Session):
    """Verify update_company rolls back transaction on database error."""
    user = _create_test_user(db_session, "update_rollback@example.com")
    service = CompanyService(db_session)
    comp = service.create_company(user.id, name="Rollback Corp")

    with patch.object(db_session, "rollback") as mock_rollback:
        with patch("app.repositories.company_repository.update", side_effect=OperationalError("DB failure", {}, None)):
            with pytest.raises(OperationalError):
                service.update_company(comp.id, user.id, location="Crash")

        mock_rollback.assert_called_once()


# ============================================================================
# 5. Company Archive Tests
# ============================================================================


def test_service_archive_company_success(db_session: Session):
    """Verify archive_company marks is_archived=True and commits."""
    user = _create_test_user(db_session, "archivist@example.com")
    service = CompanyService(db_session)
    comp = service.create_company(user.id, name="Defunct Startup")

    archived = service.archive_company(comp.id, user.id)
    assert archived.is_archived is True

    # Verify state in DB
    refreshed = service.get_company(comp.id, user.id)
    assert refreshed.is_archived is True


def test_service_archive_already_archived_company_is_idempotent(db_session: Session):
    """Verify re-archiving an already archived company returns safely without error."""
    user = _create_test_user(db_session, "repeat_archivist@example.com")
    service = CompanyService(db_session)
    comp = service.create_company(user.id, name="Old Co")

    archived1 = service.archive_company(comp.id, user.id)
    assert archived1.is_archived is True

    # Second call returns safely
    archived2 = service.archive_company(comp.id, user.id)
    assert archived2.is_archived is True
    assert archived2.id == comp.id


def test_service_archive_company_another_user_raises_not_found(db_session: Session):
    """Verify archive_company raises CompanyNotFoundError for another user's company."""
    user1 = _create_test_user(db_session, "victim_archive@example.com")
    user2 = _create_test_user(db_session, "attacker_archive@example.com")
    service = CompanyService(db_session)

    comp = service.create_company(user1.id, name="Target Co")

    with pytest.raises(CompanyNotFoundError):
        service.archive_company(comp.id, user2.id)

    db_session.refresh(comp)
    assert comp.is_archived is False


def test_service_archive_company_rollback_on_database_error(db_session: Session):
    """Verify archive_company rolls back transaction on database error."""
    user = _create_test_user(db_session, "archive_err@example.com")
    service = CompanyService(db_session)
    comp = service.create_company(user.id, name="Fail Archive")

    with patch.object(db_session, "rollback") as mock_rollback:
        with patch("app.repositories.company_repository.archive", side_effect=OperationalError("Archive DB fail", {}, None)):
            with pytest.raises(OperationalError):
                service.archive_company(comp.id, user.id)

        mock_rollback.assert_called_once()

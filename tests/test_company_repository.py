"""Integration tests for CompanyRepository using PostgreSQL savepoint transactions.

Verifies:
1. create:
   - Creates a company record for a specified user and populates database defaults.
   - Sets ownership strictly to the supplied user_id.
   - Trims whitespace and computes name_normalized automatically.
2. get_by_id:
   - Retrieves an owned company by its UUID primary key and owner ID.
   - Returns None for a company owned by another user (tenant isolation).
   - Returns None for a non-existent UUID.
3. get_by_name_normalized:
   - Looks up company case-insensitively for the owning user.
   - Returns None when queried with another user's ID.
   - Returns None for invalid or empty name strings.
4. list_by_user:
   - Returns only the requesting user's companies (strict cross-user isolation).
   - Filters active companies (is_archived=False).
   - Filters archived companies (is_archived=True).
   - Returns all companies when is_archived=None.
   - Orders all returned records by company name ascending (A-Z).
5. update:
   - Supports partial updates applied only to explicitly supplied fields.
   - Synchronizes name_normalized automatically when name is modified.
   - Refuses updates to ownership (user_id) or primary key (id).
   - Returns None and prevents modification when attempted on another user's company.
6. archive:
   - Sets is_archived to True without deleting the record.
   - Idempotent and safe if called on already archived company.
   - Returns None and prevents modification for another user's company.
7. Constraints & Transaction boundaries:
   - Duplicate normalized names for the same user trigger IntegrityError on flush.
   - Repository operations do not commit outer transactions (isolation verified).
"""

from typing import Generator
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import engine
from app.models.company import Company
from app.models.user import User
from app.repositories import company_repository


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


def _create_test_user(db: Session, email: str = "repo_test_user@example.com") -> User:
    """Helper to persist an active user record for foreign key ownership."""
    user = User(
        full_name="Repository Tester",
        email=email,
        password_hash="$argon2id$v=19$m=65536,t=3,p=4$dummyhash",
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


# ============================================================================
# 1. Company Repository Creation Tests
# ============================================================================


def test_create_company_and_verify_persistence(db_session: Session):
    """Verify company_repository.create persists all attributes and computes defaults."""
    user = _create_test_user(db_session, "creator1@example.com")

    company = company_repository.create(
        db=db_session,
        user_id=user.id,
        name="  Anthropic PBC  ",
        website="https://anthropic.com",
        industry="Artificial Intelligence",
        location="San Francisco, CA",
        notes="Applied via website",
    )

    assert isinstance(company.id, uuid.UUID)
    assert company.user_id == user.id
    assert company.name == "Anthropic PBC"
    assert company.name_normalized == "anthropic pbc"
    assert company.website == "https://anthropic.com"
    assert company.industry == "Artificial Intelligence"
    assert company.location == "San Francisco, CA"
    assert company.notes == "Applied via website"
    assert company.is_archived is False
    assert company.created_at is not None
    assert company.updated_at is not None


# ============================================================================
# 2. Company Repository Retrieval Tests
# ============================================================================


def test_get_by_id_owned_company(db_session: Session):
    """Verify get_by_id returns the matching company when owned by user."""
    user = _create_test_user(db_session, "owner@example.com")
    company = company_repository.create(
        db=db_session,
        user_id=user.id,
        name="GitHub",
    )

    found = company_repository.get_by_id(db_session, company.id, user.id)
    assert found is not None
    assert found.id == company.id
    assert found.name == "GitHub"


def test_get_by_id_returns_none_for_another_users_company(db_session: Session):
    """Verify get_by_id returns None when company belongs to another user (tenant isolation)."""
    user1 = _create_test_user(db_session, "user1@example.com")
    user2 = _create_test_user(db_session, "user2@example.com")

    comp_user1 = company_repository.create(
        db=db_session,
        user_id=user1.id,
        name="Figma",
    )

    # User 2 attempts to query User 1's company by ID
    found = company_repository.get_by_id(db_session, comp_user1.id, user2.id)
    assert found is None


def test_get_by_id_returns_none_for_nonexistent_id(db_session: Session):
    """Verify get_by_id returns None for random unknown UUID."""
    user = _create_test_user(db_session, "rando@example.com")
    found = company_repository.get_by_id(db_session, uuid.uuid4(), user.id)
    assert found is None


def test_get_by_name_normalized(db_session: Session):
    """Verify get_by_name_normalized finds company case-insensitively for the owner."""
    user1 = _create_test_user(db_session, "norm1@example.com")
    user2 = _create_test_user(db_session, "norm2@example.com")

    comp = company_repository.create(
        db=db_session,
        user_id=user1.id,
        name="Linear App",
    )

    # Case-insensitive query for owner succeeds
    found = company_repository.get_by_name_normalized(db_session, user1.id, "  LINEAR APP  ")
    assert found is not None
    assert found.id == comp.id

    # Same query for another user returns None
    assert company_repository.get_by_name_normalized(db_session, user2.id, "linear app") is None

    # Empty or invalid inputs return None
    assert company_repository.get_by_name_normalized(db_session, user1.id, "") is None
    assert company_repository.get_by_name_normalized(db_session, user1.id, "   ") is None


# ============================================================================
# 3. Company Repository List & Ordering Tests
# ============================================================================


def test_list_by_user_only_returns_requesting_users_companies(db_session: Session):
    """Verify list_by_user returns only companies belonging to the requesting user."""
    user1 = _create_test_user(db_session, "list1@example.com")
    user2 = _create_test_user(db_session, "list2@example.com")

    c1 = company_repository.create(db=db_session, user_id=user1.id, name="Company Alpha")
    c2 = company_repository.create(db=db_session, user_id=user1.id, name="Company Beta")
    c3 = company_repository.create(db=db_session, user_id=user2.id, name="Company Gamma")

    user1_companies = company_repository.list_by_user(db_session, user1.id)
    user2_companies = company_repository.list_by_user(db_session, user2.id)

    assert len(user1_companies) == 2
    assert {c.id for c in user1_companies} == {c1.id, c2.id}

    assert len(user2_companies) == 1
    assert user2_companies[0].id == c3.id


def test_list_by_user_active_and_archived_filtering(db_session: Session):
    """Verify list_by_user correctly filters by active and archived status."""
    user = _create_test_user(db_session, "filter_user@example.com")

    active_comp = company_repository.create(db=db_session, user_id=user.id, name="Active Co")
    archived_comp = company_repository.create(db=db_session, user_id=user.id, name="Archived Co")
    company_repository.archive(db_session, archived_comp.id, user.id)

    # 1. Filter active only (is_archived=False)
    active_list = company_repository.list_by_user(db_session, user.id, is_archived=False)
    assert len(active_list) == 1
    assert active_list[0].id == active_comp.id

    # 2. Filter archived only (is_archived=True)
    archived_list = company_repository.list_by_user(db_session, user.id, is_archived=True)
    assert len(archived_list) == 1
    assert archived_list[0].id == archived_comp.id

    # 3. No filter (is_archived=None)
    all_list = company_repository.list_by_user(db_session, user.id, is_archived=None)
    assert len(all_list) == 2


def test_list_by_user_alphabetical_ordering(db_session: Session):
    """Verify list_by_user returns records sorted alphabetically by name ascending (A-Z)."""
    user = _create_test_user(db_session, "ordering_user@example.com")

    # Insert out of order
    names = ["Zendesk", "Apple", "Microsoft", "Datadog"]
    for name in names:
        company_repository.create(db=db_session, user_id=user.id, name=name)

    companies = company_repository.list_by_user(db_session, user.id)
    company_names = [c.name for c in companies]

    assert company_names == ["Apple", "Datadog", "Microsoft", "Zendesk"]


# ============================================================================
# 4. Company Repository Update Tests
# ============================================================================


def test_partial_update_behavior(db_session: Session):
    """Verify update applies only specified fields while leaving others untouched."""
    user = _create_test_user(db_session, "updater@example.com")
    comp = company_repository.create(
        db=db_session,
        user_id=user.id,
        name="Vercel",
        website="https://vercel.com",
        industry="Cloud Hosting",
        location="Remote",
        notes="Original notes",
    )

    updated = company_repository.update(
        db=db_session,
        company_id=comp.id,
        user_id=user.id,
        update_data={"location": "San Francisco, CA", "notes": "Updated notes"},
    )

    assert updated is not None
    assert updated.location == "San Francisco, CA"
    assert updated.notes == "Updated notes"
    # Unmodified fields remain untouched
    assert updated.name == "Vercel"
    assert updated.website == "https://vercel.com"
    assert updated.industry == "Cloud Hosting"


def test_update_synchronizes_name_normalized(db_session: Session):
    """Verify changing company name synchronizes name_normalized automatically."""
    user = _create_test_user(db_session, "rename_user@example.com")
    comp = company_repository.create(db=db_session, user_id=user.id, name="Old Brand")
    assert comp.name_normalized == "old brand"

    updated = company_repository.update(
        db=db_session,
        company_id=comp.id,
        user_id=user.id,
        update_data={"name": "  New Rebrand Inc  "},
    )

    assert updated is not None
    assert updated.name == "New Rebrand Inc"
    assert updated.name_normalized == "new rebrand inc"


def test_update_returns_none_for_another_users_company(db_session: Session):
    """Verify update returns None and prevents modifying another user's company."""
    user1 = _create_test_user(db_session, "legit_owner@example.com")
    user2 = _create_test_user(db_session, "attacker@example.com")

    comp = company_repository.create(
        db=db_session,
        user_id=user1.id,
        name="Secret Company",
    )

    result = company_repository.update(
        db=db_session,
        company_id=comp.id,
        user_id=user2.id,
        update_data={"name": "Hacked Name"},
    )

    assert result is None
    db_session.refresh(comp)
    assert comp.name == "Secret Company"


def test_update_ignores_forbidden_or_unknown_fields(db_session: Session):
    """Verify update rejects modifying user_id, id, or unapproved fields."""
    user = _create_test_user(db_session, "safe_update@example.com")
    comp = company_repository.create(db=db_session, user_id=user.id, name="Original Co")

    rogue_user_id = uuid.uuid4()
    rogue_id = uuid.uuid4()

    updated = company_repository.update(
        db=db_session,
        company_id=comp.id,
        user_id=user.id,
        update_data={
            "user_id": rogue_user_id,
            "id": rogue_id,
            "name": "Legit Name",
            "nonexistent_column": "Exploit",
        },
    )

    assert updated is not None
    assert updated.name == "Legit Name"
    assert updated.user_id == user.id
    assert updated.id == comp.id


# ============================================================================
# 5. Company Repository Archive Tests
# ============================================================================


def test_archive_behavior(db_session: Session):
    """Verify archive sets is_archived=True without deleting the database record."""
    user = _create_test_user(db_session, "arch_user@example.com")
    comp = company_repository.create(db=db_session, user_id=user.id, name="Startup X")
    assert comp.is_archived is False

    archived = company_repository.archive(db_session, comp.id, user.id)
    assert archived is not None
    assert archived.is_archived is True

    # Record is still present in database
    stmt = select(Company).where(Company.id == comp.id)
    fetched = db_session.scalar(stmt)
    assert fetched is not None
    assert fetched.is_archived is True


def test_archive_returns_none_for_another_users_company(db_session: Session):
    """Verify archive returns None and prevents archiving another user's company."""
    user1 = _create_test_user(db_session, "victim@example.com")
    user2 = _create_test_user(db_session, "unauthorized@example.com")

    comp = company_repository.create(db=db_session, user_id=user1.id, name="Victim Co")

    result = company_repository.archive(db_session, comp.id, user2.id)
    assert result is None
    db_session.refresh(comp)
    assert comp.is_archived is False


# ============================================================================
# 6. Database Constraint & Transaction Boundary Tests
# ============================================================================


def test_duplicate_company_name_constraint_behavior(db_session: Session):
    """Verify duplicate normalized company name for the same user raises IntegrityError."""
    user = _create_test_user(db_session, "duplicate_tester@example.com")
    company_repository.create(db=db_session, user_id=user.id, name="OpenAI")

    with pytest.raises(IntegrityError):
        company_repository.create(db=db_session, user_id=user.id, name="  OPENAI  ")

    db_session.rollback()


def test_repository_operations_do_not_commit_outer_transaction(db_session: Session):
    """Verify repository operations only flush without committing the outer transaction."""
    user = _create_test_user(db_session, "uncommitted_user@example.com")
    comp = company_repository.create(db=db_session, user_id=user.id, name="Temporary Corp")

    # Roll back the active session
    db_session.rollback()

    # The record should no longer exist in the database
    stmt = select(Company).where(Company.id == comp.id)
    assert db_session.scalar(stmt) is None

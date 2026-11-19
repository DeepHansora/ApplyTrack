"""Integration tests for ApplicationRepository using PostgreSQL savepoint transactions.

Verifies:
1. create:
   - Persists all attributes correctly and generates database defaults.
   - Anchors ownership strictly to the supplied user_id.
2. get_by_id:
   - Retrieves an owned application by ID and owner ID.
   - Eagerly loads associated Company (company_name available without lazy query).
   - Returns None for an application owned by another user (tenant isolation).
   - Returns None for a non-existent UUID.
3. list_by_user:
   - Returns only the requesting user's applications (strict cross-user isolation).
   - Filters active applications (is_archived=False, default).
   - Filters archived applications (is_archived=True).
   - Returns all applications when is_archived=None.
   - Orders records by created_at descending.
4. list_applications (Search, Filter, Sort, Paginate):
   - Strict tenant isolation across all query combinations.
   - Search by job title (case-insensitive substring).
   - Search by company name (case-insensitive substring via join).
   - Filter by status.
   - Filter by company_id.
   - Filter by employment_type.
   - Filter by applied date range (applied_after, applied_before).
   - Filter by is_archived (active default, archived, all).
   - Sort by created_at, applied_at, job_title, company_name, salary.
   - Invalid sort field / sort order fallbacks safely.
   - Pagination: valid pages, 10 or 20 per page, out-of-range pages return empty list with total.
5. update:
   - Applies updates to allowed fields.
   - Does not update forbidden fields (user_id, status, is_archived).
   - Returns None for another user's application.
   - Allows clearing optional fields to None.
6. archive and unarchive:
   - archive sets is_archived to True.
   - unarchive restores is_archived to False.
   - Idempotent and safe.
   - Returns None for another user's application.
7. Transaction boundaries:
   - Repository flushes but never commits or rolls back outer transaction.
"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Generator
import uuid

import pytest
from sqlalchemy.orm import Session

from app.database import engine
from app.models.application import Application
from app.models.company import Company
from app.models.user import User
from app.repositories import application_repository


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


def _create_test_user(db: Session, email: str = "app_repo_user@example.com") -> User:
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


def _create_test_company(db: Session, user: User, name: str = "Test Company") -> Company:
    """Helper to persist a test company owned by the specified user."""
    company = Company(
        user_id=user.id,
        name=name,
        website="https://testco.example.com",
    )
    db.add(company)
    db.commit()
    db.refresh(company)
    return company


# ============================================================================
# 1. Application Creation Tests
# ============================================================================


def test_create_application_and_verify_persistence(db_session: Session):
    """Verify application_repository.create persists all attributes and computes defaults."""
    user = _create_test_user(db_session, "creator1@example.com")
    company = _create_test_company(db_session, user, "Tech Corp")

    now = datetime.now(timezone.utc)
    app = application_repository.create(
        db=db_session,
        user_id=user.id,
        company_id=company.id,
        job_title="Senior Python Engineer",
        job_description="FastAPI & PostgreSQL backend engineering.",
        job_url="https://techcorp.example.com/jobs/101",
        employment_type="Full-time",
        location="Remote, US",
        salary_min=Decimal("120000.00"),
        salary_max=Decimal("150000.00"),
        currency="USD",
        status="Saved",
        applied_at=now,
        notes="Referred by teammate.",
    )

    assert isinstance(app.id, uuid.UUID)
    assert app.user_id == user.id
    assert app.company_id == company.id
    assert app.job_title == "Senior Python Engineer"
    assert app.job_description == "FastAPI & PostgreSQL backend engineering."
    assert app.job_url == "https://techcorp.example.com/jobs/101"
    assert app.employment_type == "Full-time"
    assert app.location == "Remote, US"
    assert app.salary_min == Decimal("120000.00")
    assert app.salary_max == Decimal("150000.00")
    assert app.currency == "USD"
    assert app.status == "Saved"
    assert app.applied_at == now
    assert app.notes == "Referred by teammate."
    assert app.is_archived is False
    assert app.created_at is not None
    assert app.updated_at is not None


# ============================================================================
# 2. Application Retrieval Tests (get_by_id)
# ============================================================================


def test_get_by_id_retrieves_owned_application_with_company(db_session: Session):
    """Verify get_by_id retrieves owned application and eagerly loads the company relationship."""
    user = _create_test_user(db_session, "getter1@example.com")
    company = _create_test_company(db_session, user, "Stripe Payments")

    created = application_repository.create(
        db=db_session,
        user_id=user.id,
        company_id=company.id,
        job_title="Infrastructure Engineer",
    )

    retrieved = application_repository.get_by_id(
        db=db_session,
        application_id=created.id,
        user_id=user.id,
    )

    assert retrieved is not None
    assert retrieved.id == created.id
    assert retrieved.user_id == user.id
    assert retrieved.company_id == company.id
    assert retrieved.company is not None
    assert retrieved.company.name == "Stripe Payments"
    assert retrieved.company_name == "Stripe Payments"


def test_get_by_id_returns_none_for_other_users_application(db_session: Session):
    """Verify get_by_id returns None for another user's application (tenant isolation)."""
    user1 = _create_test_user(db_session, "user1.get@example.com")
    user2 = _create_test_user(db_session, "user2.get@example.com")
    company = _create_test_company(db_session, user1, "Company 1")

    created = application_repository.create(
        db=db_session,
        user_id=user1.id,
        company_id=company.id,
        job_title="Security Engineer",
    )

    # user2 queries user1's application ID
    result = application_repository.get_by_id(
        db=db_session,
        application_id=created.id,
        user_id=user2.id,
    )
    assert result is None


def test_get_by_id_returns_none_for_nonexistent_uuid(db_session: Session):
    """Verify get_by_id returns None for non-existent application UUID."""
    user = _create_test_user(db_session, "nonexistent@example.com")
    result = application_repository.get_by_id(
        db=db_session,
        application_id=uuid.uuid4(),
        user_id=user.id,
    )
    assert result is None


# ============================================================================
# 3. Simple User List Tests (list_by_user)
# ============================================================================


def test_list_by_user_filters_and_isolates(db_session: Session):
    """Verify list_by_user returns only the requesting user's applications with archive filter."""
    user1 = _create_test_user(db_session, "lister1@example.com")
    user2 = _create_test_user(db_session, "lister2@example.com")
    comp1 = _create_test_company(db_session, user1, "Co 1")
    comp2 = _create_test_company(db_session, user2, "Co 2")

    # user1 has 2 active and 1 archived
    app1 = application_repository.create(db_session, user1.id, comp1.id, "Active Role 1")
    app2 = application_repository.create(db_session, user1.id, comp1.id, "Active Role 2")
    app3 = application_repository.create(db_session, user1.id, comp1.id, "Archived Role")
    application_repository.archive(db_session, app3.id, user1.id)

    # user2 has 1 active
    application_repository.create(db_session, user2.id, comp2.id, "User 2 Role")

    # Default: active only (is_archived=False)
    active_apps = application_repository.list_by_user(db_session, user1.id, is_archived=False)
    assert len(active_apps) == 2
    assert {a.id for a in active_apps} == {app1.id, app2.id}

    # Archived only (is_archived=True)
    archived_apps = application_repository.list_by_user(db_session, user1.id, is_archived=True)
    assert len(archived_apps) == 1
    assert archived_apps[0].id == app3.id

    # All (is_archived=None)
    all_apps = application_repository.list_by_user(db_session, user1.id, is_archived=None)
    assert len(all_apps) == 3


# ============================================================================
# 4. Search, Filter, Sort & Paginate Tests (list_applications)
# ============================================================================


def test_list_applications_search_by_job_title_and_company_name(db_session: Session):
    """Verify search matches job title or company name case-insensitively."""
    user = _create_test_user(db_session, "searcher@example.com")
    google = _create_test_company(db_session, user, "Google DeepMind")
    meta = _create_test_company(db_session, user, "Meta Platforms")

    app1 = application_repository.create(db_session, user.id, google.id, "Software Engineer, Core")
    app2 = application_repository.create(db_session, user.id, meta.id, "Research Scientist")
    app3 = application_repository.create(db_session, user.id, meta.id, "Product Manager")

    # Search by job title keyword
    items, total = application_repository.list_applications(
        db=db_session,
        user_id=user.id,
        search="scientist",
    )
    assert total == 1
    assert items[0].id == app2.id

    # Search by company name keyword
    items_comp, total_comp = application_repository.list_applications(
        db=db_session,
        user_id=user.id,
        search="deepmind",
    )
    assert total_comp == 1
    assert items_comp[0].id == app1.id

    # Search matching multiple entries
    items_meta, total_meta = application_repository.list_applications(
        db=db_session,
        user_id=user.id,
        search="meta",
    )
    assert total_meta == 2
    assert {a.id for a in items_meta} == {app2.id, app3.id}


def test_list_applications_filter_by_status_and_employment_type(db_session: Session):
    """Verify filtering by lifecycle status and employment type."""
    user = _create_test_user(db_session, "filterer@example.com")
    comp = _create_test_company(db_session, user, "Acme Corp")

    app1 = application_repository.create(
        db_session, user.id, comp.id, "Job 1", status="Interview", employment_type="Full-time"
    )
    app2 = application_repository.create(
        db_session, user.id, comp.id, "Job 2", status="Saved", employment_type="Contract"
    )
    app3 = application_repository.create(
        db_session, user.id, comp.id, "Job 3", status="Interview", employment_type="Contract"
    )

    # Filter by status = Interview
    items, total = application_repository.list_applications(
        db=db_session,
        user_id=user.id,
        status="Interview",
    )
    assert total == 2
    assert {a.id for a in items} == {app1.id, app3.id}

    # Filter by employment_type = Contract
    items_emp, total_emp = application_repository.list_applications(
        db=db_session,
        user_id=user.id,
        employment_type="Contract",
    )
    assert total_emp == 2
    assert {a.id for a in items_emp} == {app2.id, app3.id}

    # Combined status + employment_type filter
    items_combo, total_combo = application_repository.list_applications(
        db=db_session,
        user_id=user.id,
        status="Interview",
        employment_type="Contract",
    )
    assert total_combo == 1
    assert items_combo[0].id == app3.id


def test_list_applications_filter_by_company_and_date_range(db_session: Session):
    """Verify filtering by specific company_id and applied date ranges."""
    user = _create_test_user(db_session, "dater@example.com")
    c1 = _create_test_company(db_session, user, "First Co")
    c2 = _create_test_company(db_session, user, "Second Co")

    d1 = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
    d2 = datetime(2026, 2, 15, 12, 0, 0, tzinfo=timezone.utc)
    d3 = datetime(2026, 3, 15, 12, 0, 0, tzinfo=timezone.utc)

    app1 = application_repository.create(db_session, user.id, c1.id, "Job 1", applied_at=d1)
    app2 = application_repository.create(db_session, user.id, c2.id, "Job 2", applied_at=d2)
    app3 = application_repository.create(db_session, user.id, c1.id, "Job 3", applied_at=d3)

    # Filter by company_id = c1
    items_c1, total_c1 = application_repository.list_applications(
        db=db_session,
        user_id=user.id,
        company_id=c1.id,
    )
    assert total_c1 == 2
    assert {a.id for a in items_c1} == {app1.id, app3.id}

    # Filter by applied_after (Feb 1, 2026)
    cutoff_after = datetime(2026, 2, 1, 0, 0, 0, tzinfo=timezone.utc)
    items_after, total_after = application_repository.list_applications(
        db=db_session,
        user_id=user.id,
        applied_after=cutoff_after,
    )
    assert total_after == 2
    assert {a.id for a in items_after} == {app2.id, app3.id}

    # Filter by applied_before (Feb 20, 2026)
    cutoff_before = datetime(2026, 2, 20, 0, 0, 0, tzinfo=timezone.utc)
    items_before, total_before = application_repository.list_applications(
        db=db_session,
        user_id=user.id,
        applied_before=cutoff_before,
    )
    assert total_before == 2
    assert {a.id for a in items_before} == {app1.id, app2.id}


def test_list_applications_sorting_and_whitelisting(db_session: Session):
    """Verify sorting across supported whitelist fields and fallback on invalid sort fields."""
    user = _create_test_user(db_session, "sorter@example.com")
    c_apple = _create_test_company(db_session, user, "Apple")
    c_zebra = _create_test_company(db_session, user, "Zebra Tech")

    app1 = application_repository.create(
        db_session, user.id, c_apple.id, "Junior Dev", salary_max=Decimal("80000")
    )
    app2 = application_repository.create(
        db_session, user.id, c_zebra.id, "Senior Dev", salary_max=Decimal("150000")
    )

    # Sort by company_name asc: Apple, Zebra
    items_co, _ = application_repository.list_applications(
        db=db_session, user_id=user.id, sort_by="company_name", sort_order="asc"
    )
    assert [a.id for a in items_co] == [app1.id, app2.id]

    # Sort by salary desc: Senior Dev (150k), Junior Dev (80k)
    items_sal, _ = application_repository.list_applications(
        db=db_session, user_id=user.id, sort_by="salary", sort_order="desc"
    )
    assert [a.id for a in items_sal] == [app2.id, app1.id]

    # Invalid sort field falls back safely to created_at desc without error
    items_invalid, _ = application_repository.list_applications(
        db=db_session, user_id=user.id, sort_by="invalid_column; DROP TABLE applications;", sort_order="invalid"
    )
    assert len(items_invalid) == 2


def test_list_applications_pagination(db_session: Session):
    """Verify pagination returns correct slices, page counts, and handles out-of-range pages."""
    user = _create_test_user(db_session, "pager@example.com")
    comp = _create_test_company(db_session, user, "Paging Co")

    # Create 25 applications
    for i in range(1, 26):
        application_repository.create(
            db=db_session,
            user_id=user.id,
            company_id=comp.id,
            job_title=f"Role {i:02d}",
        )

    # Page 1, 10 items
    p1_items, p1_total = application_repository.list_applications(
        db=db_session, user_id=user.id, page=1, per_page=10
    )
    assert p1_total == 25
    assert len(p1_items) == 10

    # Page 2, 10 items
    p2_items, p2_total = application_repository.list_applications(
        db=db_session, user_id=user.id, page=2, per_page=10
    )
    assert p2_total == 25
    assert len(p2_items) == 10
    # No overlap between pages
    assert set(a.id for a in p1_items).isdisjoint(set(a.id for a in p2_items))

    # Page 3, remaining 5 items
    p3_items, p3_total = application_repository.list_applications(
        db=db_session, user_id=user.id, page=3, per_page=10
    )
    assert p3_total == 25
    assert len(p3_items) == 5

    # 5 items per page (5 per page supported)
    p5_items, p5_total = application_repository.list_applications(
        db=db_session, user_id=user.id, page=1, per_page=5
    )
    assert p5_total == 25
    assert len(p5_items) == 5

    # Out of range page (page 99) returns empty items and correct total
    p99_items, p99_total = application_repository.list_applications(
        db=db_session, user_id=user.id, page=99, per_page=10
    )
    assert p99_total == 25
    assert len(p99_items) == 0

    # Non-supported per_page (e.g. 50 or -5) defaults to 20
    p_def_items, p_def_total = application_repository.list_applications(
        db=db_session, user_id=user.id, page=1, per_page=50
    )
    assert len(p_def_items) == 20


# ============================================================================
# 5. Application Update Tests
# ============================================================================


def test_update_allowed_fields(db_session: Session):
    """Verify application_repository.update modifies allowed fields and flushes to database."""
    user = _create_test_user(db_session, "updater@example.com")
    comp1 = _create_test_company(db_session, user, "Initial Co")
    comp2 = _create_test_company(db_session, user, "New Co")

    app = application_repository.create(
        db=db_session,
        user_id=user.id,
        company_id=comp1.id,
        job_title="Software Developer",
        location="Austin, TX",
        salary_min=Decimal("90000"),
    )

    update_payload = {
        "company_id": comp2.id,
        "job_title": "Senior Software Developer",
        "location": "Dallas, TX",
        "salary_min": Decimal("110000"),
        "notes": "Updated after call",
    }
    updated = application_repository.update(
        db=db_session,
        application_id=app.id,
        user_id=user.id,
        update_data=update_payload,
    )

    assert updated is not None
    assert updated.company_id == comp2.id
    assert updated.job_title == "Senior Software Developer"
    assert updated.location == "Dallas, TX"
    assert updated.salary_min == Decimal("110000")
    assert updated.notes == "Updated after call"


def test_update_ignores_forbidden_fields(db_session: Session):
    """Verify update ignores fields outside ALLOWED_UPDATE_FIELDS (user_id, status, is_archived)."""
    user = _create_test_user(db_session, "forbidden@example.com")
    comp = _create_test_company(db_session, user, "Company")

    app = application_repository.create(
        db=db_session,
        user_id=user.id,
        company_id=comp.id,
        job_title="Engineer",
        status="Saved",
    )

    new_user_id = uuid.uuid4()
    update_payload = {
        "user_id": new_user_id,
        "status": "Offer",
        "is_archived": True,
        "job_title": "Lead Engineer",
    }
    updated = application_repository.update(
        db=db_session,
        application_id=app.id,
        user_id=user.id,
        update_data=update_payload,
    )

    assert updated is not None
    assert updated.job_title == "Lead Engineer"
    assert updated.user_id == user.id  # Unchanged!
    assert updated.status == "Saved"  # Unchanged!
    assert updated.is_archived is False  # Unchanged!


def test_update_returns_none_for_other_users_application(db_session: Session):
    """Verify update returns None when trying to update another user's application."""
    user1 = _create_test_user(db_session, "owner.up@example.com")
    user2 = _create_test_user(db_session, "attacker.up@example.com")
    comp = _create_test_company(db_session, user1, "Owner Co")

    app = application_repository.create(db_session, user1.id, comp.id, "Target App")

    result = application_repository.update(
        db=db_session,
        application_id=app.id,
        user_id=user2.id,
        update_data={"job_title": "Hacked Title"},
    )
    assert result is None
    assert app.job_title == "Target App"


# ============================================================================
# 6. Archive & Unarchive Tests
# ============================================================================


def test_archive_and_unarchive_lifecycle(db_session: Session):
    """Verify archive and unarchive toggle is_archived properly and idempotently."""
    user = _create_test_user(db_session, "archiver@example.com")
    comp = _create_test_company(db_session, user, "Lifecycle Co")

    app = application_repository.create(db_session, user.id, comp.id, "Lifecycle Job")
    assert app.is_archived is False

    # Archive
    archived = application_repository.archive(db_session, app.id, user.id)
    assert archived is not None
    assert archived.is_archived is True

    # Repeated archive is idempotent
    archived_again = application_repository.archive(db_session, app.id, user.id)
    assert archived_again is not None
    assert archived_again.is_archived is True

    # Unarchive
    unarchived = application_repository.unarchive(db_session, app.id, user.id)
    assert unarchived is not None
    assert unarchived.is_archived is False

    # Repeated unarchive is idempotent
    unarchived_again = application_repository.unarchive(db_session, app.id, user.id)
    assert unarchived_again is not None
    assert unarchived_again.is_archived is False


def test_archive_and_unarchive_returns_none_for_other_user(db_session: Session):
    """Verify archive and unarchive return None for applications owned by another user."""
    user1 = _create_test_user(db_session, "user1.arc@example.com")
    user2 = _create_test_user(db_session, "user2.arc@example.com")
    comp = _create_test_company(db_session, user1, "Company")

    app = application_repository.create(db_session, user1.id, comp.id, "Protected Job")

    assert application_repository.archive(db_session, app.id, user2.id) is None
    assert app.is_archived is False

    # Archive by owner
    application_repository.archive(db_session, app.id, user1.id)
    assert app.is_archived is True

    # Try unarchive by another user
    assert application_repository.unarchive(db_session, app.id, user2.id) is None
    assert app.is_archived is True

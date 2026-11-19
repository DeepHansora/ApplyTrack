"""Focused unit and database integration tests for Application SQLAlchemy model and Pydantic schemas.

Covers:
Model tests:
1. Application model defines all required columns with expected types, nullability, and defaults.
2. Foreign key constraints:
   - Application.user_id references users.id with ON DELETE CASCADE.
   - Application.company_id references companies.id with ON DELETE RESTRICT.
3. Check constraints defined on status, employment_type, and salary ranges.
4. Covering and search indexes on user_id, status, applied_at, company_id, and is_archived.
5. User.applications, Company.applications, and Application relationships.
6. UUIDPrimaryKeyMixin and TimestampMixin inheritance and timezone-aware timestamps.
7. Model-level validators:
   - job_title trimming and empty/length validation.
   - status validation against approved set.
   - employment_type validation.
   - currency: exactly 3 uppercase alphabetic characters (rejects invalid lengths and non-alphabetic).
   - applied_at: rejects naive datetimes, accepts timezone-aware datetimes.
   - company_name property populated from loaded company relationship.

Database constraint tests (PostgreSQL):
8. Persisting an application with required and optional fields.
9. Deleting a user cascades and deletes all associated application records.
10. Deleting a company with associated applications is restricted (IntegrityError).
11. Invalid status rejected by PostgreSQL check constraint.
12. Invalid employment_type rejected by PostgreSQL check constraint.
13. salary_min > salary_max rejected by PostgreSQL check constraint.
14. Negative salary values rejected by PostgreSQL check constraints.

Schema tests:
15. ApplicationCreate accepts valid payload with default status 'Saved' and default currency 'USD'.
16. ApplicationCreate accepts valid payload with custom initial status and custom currency.
17. ApplicationCreate currency validation: exactly 3 uppercase alphabetic chars, rejects invalid lengths / non-alpha.
18. ApplicationCreate applied_at validation: rejects naive datetime and naive ISO string, accepts aware datetime / ISO string.
19. ApplicationCreate trims optional text fields and normalizes empty strings to None.
20. ApplicationCreate rejects empty or whitespace-only job_title.
21. ApplicationCreate validates job_title length boundaries (1-255 characters).
22. ApplicationCreate rejects invalid status and invalid employment_type.
23. ApplicationCreate validates salary ranges (salary_min <= salary_max, non-negative).
24. ApplicationCreate rejects forbidden/server-managed fields (user_id, id, is_archived, etc.).
25. ApplicationUpdate allows valid partial updates.
26. ApplicationUpdate rejects company_id=None while omitted company_id remains valid.
27. ApplicationUpdate rejects explicit job_title=None while omitted job_title remains valid.
28. ApplicationUpdate rejects status field (reserved for Phase 4).
29. ApplicationUpdate rejects is_archived field (archiving/unarchiving separate operations).
30. ApplicationUpdate currency validation: accepts valid 3-letter codes, rejects invalid length/non-alpha.
31. ApplicationUpdate applied_at validation: rejects naive datetime, accepts timezone-aware.
32. ApplicationUpdate allows clearing optional fields (job_description, notes, etc.).
33. ApplicationUpdate partial salary update allows single bounds and validates min <= max when both provided.
34. ApplicationOut serializes accurately from ORM model instances and populates company_name from company relationship.
"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Generator
import uuid

import pytest
from pydantic import ValidationError
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import NO_VALUE
from sqlalchemy.orm.exc import DetachedInstanceError

from app.database import engine
from app.models.application import (
    APPLICATION_STATUSES,
    EMPLOYMENT_TYPES,
    Application,
)
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin
from app.models.company import Company
from app.models.user import User
from app.schemas.application import (
    ApplicationCreate,
    ApplicationOut,
    ApplicationUpdate,
)


# ============================================================================
# Database Session Fixture (PostgreSQL with Savepoint Rollback)
# ============================================================================


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


def _create_test_user(db: Session, email: str = "applicant@example.com") -> User:
    """Helper to persist an active test user for ownership foreign keys."""
    user = User(
        full_name="Applicant User",
        email=email,
        password_hash="$argon2id$v=19$m=65536,t=3,p=4$dummyhash",
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _create_test_company(db: Session, user: User, name: str = "Acme Corp") -> Company:
    """Helper to persist a test company owned by the test user."""
    company = Company(
        user_id=user.id,
        name=name,
        website="https://acme.example.com",
    )
    db.add(company)
    db.commit()
    db.refresh(company)
    return company


# ============================================================================
# 1. SQLAlchemy Model Structure & Metadata Tests
# ============================================================================


def test_application_model_columns():
    """Verify Application model defines all required columns with expected types and nullability."""
    cols = Application.__table__.columns

    expected_columns = {
        "id",
        "user_id",
        "company_id",
        "job_title",
        "job_description",
        "job_url",
        "employment_type",
        "location",
        "salary_min",
        "salary_max",
        "currency",
        "status",
        "applied_at",
        "notes",
        "is_archived",
        "created_at",
        "updated_at",
    }
    assert expected_columns.issubset(set(cols.keys()))

    # Nullability assertions
    assert cols["id"].primary_key is True
    assert cols["user_id"].nullable is False
    assert cols["company_id"].nullable is False
    assert cols["job_title"].nullable is False
    assert cols["job_description"].nullable is True
    assert cols["job_url"].nullable is True
    assert cols["employment_type"].nullable is True
    assert cols["location"].nullable is True
    assert cols["salary_min"].nullable is True
    assert cols["salary_max"].nullable is True
    assert cols["currency"].nullable is True
    assert cols["status"].nullable is False
    assert cols["applied_at"].nullable is True
    assert cols["notes"].nullable is True
    assert cols["is_archived"].nullable is False
    assert cols["created_at"].nullable is False
    assert cols["updated_at"].nullable is False

    # Defaults
    assert cols["status"].default.arg == "Saved"
    assert cols["is_archived"].default.arg is False
    assert cols["currency"].default.arg == "USD"


def test_application_foreign_keys():
    """Verify user_id cascades on delete and company_id restricts on delete."""
    cols = Application.__table__.columns

    # user_id -> users.id (CASCADE)
    user_fk = list(cols["user_id"].foreign_keys)[0]
    assert user_fk.target_fullname == "users.id"
    assert user_fk.ondelete.upper() == "CASCADE"

    # company_id -> companies.id (RESTRICT)
    company_fk = list(cols["company_id"].foreign_keys)[0]
    assert company_fk.target_fullname == "companies.id"
    assert company_fk.ondelete.upper() == "RESTRICT"


def test_application_constraints_and_indexes():
    """Verify check constraints and indexes on applications table."""
    table = Application.__table__

    # Check constraints
    ck_names = {ck.name for ck in table.constraints if hasattr(ck, "name") and ck.name}
    assert "ck_applications_status" in ck_names
    assert "ck_applications_employment_type" in ck_names
    assert "ck_applications_salary_range" in ck_names
    assert "ck_applications_salary_min_non_negative" in ck_names
    assert "ck_applications_salary_max_non_negative" in ck_names

    # Indexes
    index_names = {idx.name for idx in table.indexes}
    assert "idx_applications_user_id_status" in index_names
    assert "idx_applications_user_id_applied_at" in index_names
    assert "idx_applications_company_id" in index_names
    assert "idx_applications_user_id_is_archived" in index_names


def test_application_relationships():
    """Verify bidirectional relationships between User, Company, and Application."""
    assert hasattr(User, "applications")
    assert hasattr(Company, "applications")
    assert hasattr(Application, "user")
    assert hasattr(Application, "company")

    user_rel = User.__mapper__.relationships["applications"]
    assert user_rel.target == Application.__table__
    assert user_rel.back_populates == "user"
    assert "delete-orphan" in user_rel.cascade

    company_rel = Company.__mapper__.relationships["applications"]
    assert company_rel.target == Application.__table__
    assert company_rel.back_populates == "company"


def test_application_mixins():
    """Verify Application inherits from UUIDPrimaryKeyMixin and TimestampMixin."""
    assert issubclass(Application, UUIDPrimaryKeyMixin)
    assert issubclass(Application, TimestampMixin)


def test_application_model_validators():
    """Verify Python model-level validators on Application."""
    aware_now = datetime.now(timezone.utc)
    app = Application(
        user_id=uuid.uuid4(),
        company_id=uuid.uuid4(),
        job_title="  Backend Engineer  ",
        status="Saved",
        employment_type="  Full-time  ",
        currency="  usd  ",
        applied_at=aware_now,
    )
    assert app.job_title == "Backend Engineer"
    assert app.employment_type == "Full-time"
    assert app.currency == "USD"
    assert app.applied_at == aware_now

    # Blank job title rejected
    with pytest.raises(ValueError, match="Job title cannot be empty"):
        Application(
            user_id=uuid.uuid4(),
            company_id=uuid.uuid4(),
            job_title="   ",
        )

    # Job title > 255 rejected
    with pytest.raises(ValueError, match="cannot exceed 255"):
        Application(
            user_id=uuid.uuid4(),
            company_id=uuid.uuid4(),
            job_title="X" * 256,
        )

    # Invalid status rejected
    with pytest.raises(ValueError, match="Invalid status"):
        Application(
            user_id=uuid.uuid4(),
            company_id=uuid.uuid4(),
            job_title="Engineer",
            status="UnknownStatus",
        )

    # Invalid employment type rejected
    with pytest.raises(ValueError, match="Invalid employment type"):
        Application(
            user_id=uuid.uuid4(),
            company_id=uuid.uuid4(),
            job_title="Engineer",
            employment_type="GigWork",
        )


def test_application_model_currency_validation():
    """Verify Application model currency validator enforces 3 uppercase alphabetic chars."""
    u_id = uuid.uuid4()
    c_id = uuid.uuid4()

    # Valid currencies normalize to uppercase
    app = Application(user_id=u_id, company_id=c_id, job_title="Dev", currency="  eur  ")
    assert app.currency == "EUR"

    # Reject invalid lengths
    with pytest.raises(ValueError, match="exactly three alphabetic characters"):
        Application(user_id=u_id, company_id=c_id, job_title="Dev", currency="US")

    with pytest.raises(ValueError, match="exactly three alphabetic characters"):
        Application(user_id=u_id, company_id=c_id, job_title="Dev", currency="USDD")

    with pytest.raises(ValueError, match="exactly three alphabetic characters"):
        Application(user_id=u_id, company_id=c_id, job_title="Dev", currency="   ")

    # Reject non-alphabetic
    with pytest.raises(ValueError, match="exactly three alphabetic characters"):
        Application(user_id=u_id, company_id=c_id, job_title="Dev", currency="US1")

    with pytest.raises(ValueError, match="exactly three alphabetic characters"):
        Application(user_id=u_id, company_id=c_id, job_title="Dev", currency="123")


def test_application_model_applied_at_rejects_naive_datetime():
    """Verify Application model validator rejects naive datetimes for applied_at."""
    u_id = uuid.uuid4()
    c_id = uuid.uuid4()

    # Naive datetime rejected
    naive_dt = datetime(2026, 5, 1, 10, 0, 0)
    with pytest.raises(ValueError, match="applied_at must be a timezone-aware datetime"):
        Application(user_id=u_id, company_id=c_id, job_title="Dev", applied_at=naive_dt)

    # Timezone-aware datetime accepted
    aware_dt = datetime(2026, 5, 1, 10, 0, 0, tzinfo=timezone.utc)
    app = Application(user_id=u_id, company_id=c_id, job_title="Dev", applied_at=aware_dt)
    assert app.applied_at == aware_dt


def test_application_model_company_name_loaded_returns_name():
    """Verify company_name returns company name when relationship is loaded with a company."""
    app = Application(
        user_id=uuid.uuid4(),
        company_id=uuid.uuid4(),
        job_title="Software Architect",
    )
    comp = Company(name="GitHub Inc")
    app.company = comp
    assert app.company_name == "GitHub Inc"


def test_application_model_company_name_loaded_none_returns_none():
    """Verify company_name returns None when relationship is loaded with None."""
    app = Application(
        user_id=uuid.uuid4(),
        company_id=uuid.uuid4(),
        job_title="Software Architect",
        company=None,
    )
    assert inspect(app).attrs.company.loaded_value is None
    assert app.company_name is None


def test_application_model_company_name_unloaded_returns_none_without_query(db_session: Session):
    """Verify company_name returns None when unloaded, using NO_VALUE and never issuing a query."""
    user = _create_test_user(db_session, "unloaded.test@example.com")
    comp = _create_test_company(db_session, user, "Lazy Corp")
    app = Application(
        user_id=user.id,
        company_id=comp.id,
        job_title="Backend Engineer",
    )
    db_session.add(app)
    db_session.commit()
    app_id = app.id

    # Expire all attributes so company relationship is unloaded
    db_session.expire_all()
    queried_app = db_session.get(Application, app_id)

    # Verify company relationship is unloaded (NO_VALUE)
    state = inspect(queried_app)
    assert state.attrs.company.loaded_value is NO_VALUE

    # Accessing company_name must return None and NOT trigger a lazy load
    assert queried_app.company_name is None
    assert state.attrs.company.loaded_value is NO_VALUE


def test_application_model_company_name_detached_unloaded_returns_none_without_error(db_session: Session):
    """Verify company_name returns None on a detached instance with unloaded relationship without raising DetachedInstanceError."""
    user = _create_test_user(db_session, "detached.test@example.com")
    comp = _create_test_company(db_session, user, "Detached Corp")
    app = Application(
        user_id=user.id,
        company_id=comp.id,
        job_title="Frontend Engineer",
    )
    db_session.add(app)
    db_session.commit()
    app_id = app.id

    # Expire all attributes to unload company, then query
    db_session.expire_all()
    queried_app = db_session.get(Application, app_id)
    assert inspect(queried_app).attrs.company.loaded_value is NO_VALUE

    # Detach instance completely from session
    db_session.expunge(queried_app)

    # Directly accessing .company would raise DetachedInstanceError
    with pytest.raises(DetachedInstanceError):
        _ = queried_app.company

    # But company_name safely returns None using NO_VALUE check without raising an exception
    assert queried_app.company_name is None

    # ApplicationOut serialization also safely returns None
    out = ApplicationOut.model_validate(queried_app)
    assert out.company_name is None


def test_application_repr():
    """Verify safe string representation."""
    app_id = uuid.uuid4()
    u_id = uuid.uuid4()
    c_id = uuid.uuid4()
    app = Application(
        id=app_id,
        user_id=u_id,
        company_id=c_id,
        job_title="Staff Software Engineer",
        status="Interview",
        is_archived=False,
    )
    rep = repr(app)
    assert "Staff Software Engineer" in rep
    assert "Interview" in rep
    assert str(app_id) in rep


# ============================================================================
# 2. Database Constraint & Integrity Tests (PostgreSQL)
# ============================================================================


def test_application_persistence_and_defaults(db_session: Session):
    """Verify application can be persisted in PostgreSQL with expected defaults."""
    user = _create_test_user(db_session, "user.app1@example.com")
    company = _create_test_company(db_session, user, "Tech Corp")

    app = Application(
        user_id=user.id,
        company_id=company.id,
        job_title="Senior Python Developer",
        job_description="Build distributed systems with FastAPI and PostgreSQL.",
        job_url="https://techcorp.example.com/jobs/123",
        employment_type="Full-time",
        location="Remote, US",
        salary_min=Decimal("130000.00"),
        salary_max=Decimal("160000.00"),
        currency="USD",
        notes="Applied with internal recommendation.",
    )
    db_session.add(app)
    db_session.commit()
    db_session.refresh(app)

    assert isinstance(app.id, uuid.UUID)
    assert app.user_id == user.id
    assert app.company_id == company.id
    assert app.job_title == "Senior Python Developer"
    assert app.status == "Saved"
    assert app.is_archived is False
    assert app.salary_min == Decimal("130000.00")
    assert app.salary_max == Decimal("160000.00")
    assert app.currency == "USD"
    assert isinstance(app.created_at, datetime)
    assert isinstance(app.updated_at, datetime)
    assert app.created_at.tzinfo is not None


def test_cascade_delete_user_removes_applications(db_session: Session):
    """Verify deleting a User record cascades and removes all owned applications."""
    user = _create_test_user(db_session, "user.cascade@example.com")
    company = _create_test_company(db_session, user, "Cascade Corp")

    app = Application(
        user_id=user.id,
        company_id=company.id,
        job_title="DevOps Engineer",
    )
    db_session.add(app)
    db_session.commit()

    # Delete user
    db_session.delete(user)
    db_session.commit()

    # Application should be deleted
    stmt = select(Application).where(Application.user_id == user.id)
    assert len(db_session.scalars(stmt).all()) == 0


def test_company_deletion_restricted_when_applications_exist(db_session: Session):
    """Verify deleting a Company that has associated applications is blocked by ON DELETE RESTRICT."""
    user = _create_test_user(db_session, "user.restrict@example.com")
    company = _create_test_company(db_session, user, "Protected Corp")

    app = Application(
        user_id=user.id,
        company_id=company.id,
        job_title="Security Analyst",
    )
    db_session.add(app)
    db_session.commit()

    # Attempting to delete the company must fail with IntegrityError
    db_session.delete(company)
    with pytest.raises(IntegrityError) as exc_info:
        db_session.commit()

    assert "applications_company_id_fkey" in str(exc_info.value).lower()
    assert "foreign key constraint" in str(exc_info.value).lower()
    db_session.rollback()


def test_db_check_constraint_invalid_status_rejected(db_session: Session):
    """Verify PostgreSQL check constraint rejects invalid status."""
    user = _create_test_user(db_session, "user.ck1@example.com")
    company = _create_test_company(db_session, user, "Check Status Corp")

    app = Application(
        user_id=user.id,
        company_id=company.id,
        job_title="Engineer",
    )
    app.__dict__["status"] = "PendingReview"
    db_session.add(app)

    with pytest.raises(IntegrityError) as exc_info:
        db_session.commit()

    assert "ck_applications_status" in str(exc_info.value).lower()
    db_session.rollback()


def test_db_check_constraint_invalid_employment_type_rejected(db_session: Session):
    """Verify PostgreSQL check constraint rejects invalid employment_type."""
    user = _create_test_user(db_session, "user.ck2@example.com")
    company = _create_test_company(db_session, user, "Check Emp Corp")

    app = Application(
        user_id=user.id,
        company_id=company.id,
        job_title="Engineer",
    )
    app.__dict__["employment_type"] = "Volunteer"
    db_session.add(app)

    with pytest.raises(IntegrityError) as exc_info:
        db_session.commit()

    assert "ck_applications_employment_type" in str(exc_info.value).lower()
    db_session.rollback()


def test_db_check_constraint_salary_range_rejected(db_session: Session):
    """Verify PostgreSQL check constraint rejects salary_min > salary_max."""
    user = _create_test_user(db_session, "user.ck3@example.com")
    company = _create_test_company(db_session, user, "Check Salary Corp")

    app = Application(
        user_id=user.id,
        company_id=company.id,
        job_title="Engineer",
        salary_min=Decimal("150000.00"),
        salary_max=Decimal("100000.00"),
    )
    db_session.add(app)

    with pytest.raises(IntegrityError) as exc_info:
        db_session.commit()

    assert "ck_applications_salary_range" in str(exc_info.value).lower()
    db_session.rollback()


def test_db_check_constraint_negative_salary_rejected(db_session: Session):
    """Verify PostgreSQL check constraints reject negative salary values."""
    user = _create_test_user(db_session, "user.ck4@example.com")
    company = _create_test_company(db_session, user, "Check Neg Salary Corp")

    app = Application(
        user_id=user.id,
        company_id=company.id,
        job_title="Engineer",
        salary_min=Decimal("-500.00"),
    )
    db_session.add(app)

    with pytest.raises(IntegrityError) as exc_info:
        db_session.commit()

    assert "ck_applications_salary_min_non_negative" in str(exc_info.value).lower()
    db_session.rollback()


# ============================================================================
# 3. Pydantic Schemas Tests
# ============================================================================


def test_application_create_valid_payload():
    """Verify ApplicationCreate accepts valid payload with default status and currency."""
    comp_id = uuid.uuid4()
    payload = {
        "company_id": str(comp_id),
        "job_title": "  Backend Engineer  ",
        "job_description": "  Build services  ",
        "job_url": "  https://example.com/job/1  ",
        "employment_type": "  Full-time  ",
        "location": "  Remote  ",
        "salary_min": "100000.00",
        "salary_max": "140000.00",
        "currency": "  usd  ",
        "notes": "  Notes here  ",
    }
    schema = ApplicationCreate(**payload)

    assert schema.company_id == comp_id
    assert schema.job_title == "Backend Engineer"
    assert schema.job_description == "Build services"
    assert schema.job_url == "https://example.com/job/1"
    assert schema.employment_type == "Full-time"
    assert schema.location == "Remote"
    assert schema.salary_min == Decimal("100000.00")
    assert schema.salary_max == Decimal("140000.00")
    assert schema.currency == "USD"
    assert schema.status == "Saved"  # Default
    assert schema.notes == "Notes here"


def test_application_create_initial_custom_status():
    """Verify ApplicationCreate accepts initial status other than Saved (e.g., Applied)."""
    schema = ApplicationCreate(
        company_id=uuid.uuid4(),
        job_title="Data Scientist",
        status="Applied",
    )
    assert schema.status == "Applied"


def test_application_create_currency_validation():
    """Verify currency validation on ApplicationCreate enforces 3 uppercase alphabetic chars."""
    cid = uuid.uuid4()

    # Valid currency normalized to uppercase
    c1 = ApplicationCreate(company_id=cid, job_title="Dev", currency="  eur  ")
    assert c1.currency == "EUR"

    # Default is USD
    c_def = ApplicationCreate(company_id=cid, job_title="Dev")
    assert c_def.currency == "USD"

    # Invalid lengths rejected
    with pytest.raises(ValidationError) as exc_info:
        ApplicationCreate(company_id=cid, job_title="Dev", currency="US")
    assert any("Currency must be exactly three alphabetic characters" in str(err) for err in exc_info.value.errors())

    with pytest.raises(ValidationError):
        ApplicationCreate(company_id=cid, job_title="Dev", currency="USDD")

    with pytest.raises(ValidationError):
        ApplicationCreate(company_id=cid, job_title="Dev", currency="   ")

    # Non-alphabetic values rejected
    with pytest.raises(ValidationError):
        ApplicationCreate(company_id=cid, job_title="Dev", currency="US1")

    with pytest.raises(ValidationError):
        ApplicationCreate(company_id=cid, job_title="Dev", currency="123")

    with pytest.raises(ValidationError):
        ApplicationCreate(company_id=cid, job_title="Dev", currency="U$D")


def test_application_create_applied_at_validation():
    """Verify applied_at validation rejects naive datetimes and accepts timezone-aware datetimes."""
    cid = uuid.uuid4()

    # Aware datetime accepted
    aware_dt = datetime(2026, 4, 15, 9, 30, 0, tzinfo=timezone.utc)
    app1 = ApplicationCreate(company_id=cid, job_title="Dev", applied_at=aware_dt)
    assert app1.applied_at == aware_dt

    # Aware ISO string accepted
    app2 = ApplicationCreate(company_id=cid, job_title="Dev", applied_at="2026-04-15T09:30:00Z")
    assert app2.applied_at.tzinfo is not None

    # Naive datetime rejected
    naive_dt = datetime(2026, 4, 15, 9, 30, 0)
    with pytest.raises(ValidationError) as exc_info:
        ApplicationCreate(company_id=cid, job_title="Dev", applied_at=naive_dt)
    assert any("applied_at must be timezone-aware" in str(err) for err in exc_info.value.errors())

    # Naive ISO string rejected (no timezone offset)
    with pytest.raises(ValidationError) as exc_info2:
        ApplicationCreate(company_id=cid, job_title="Dev", applied_at="2026-04-15T09:30:00")
    assert any("applied_at must be timezone-aware" in str(err) for err in exc_info2.value.errors())


def test_application_create_trims_and_converts_empty_strings_to_none():
    """Verify optional text fields normalize empty strings to None."""
    schema = ApplicationCreate(
        company_id=uuid.uuid4(),
        job_title="Product Manager",
        job_description="   ",
        job_url="",
        employment_type="   ",
        location="   ",
        notes="",
    )
    assert schema.job_description is None
    assert schema.job_url is None
    assert schema.employment_type is None
    assert schema.location is None
    assert schema.notes is None


def test_application_create_blank_job_title_rejected():
    """Verify blank or whitespace-only job_title is rejected."""
    with pytest.raises(ValidationError) as exc_info:
        ApplicationCreate(
            company_id=uuid.uuid4(),
            job_title="   ",
        )
    assert any("Job title cannot be empty" in err["msg"] for err in exc_info.value.errors())


def test_application_create_job_title_length_boundaries():
    """Verify job_title length validation (1 to 255 chars)."""
    # 1 char accepted
    app1 = ApplicationCreate(company_id=uuid.uuid4(), job_title="A")
    assert app1.job_title == "A"

    # 255 chars accepted
    app2 = ApplicationCreate(company_id=uuid.uuid4(), job_title="B" * 255)
    assert len(app2.job_title) == 255

    # 256 chars rejected
    with pytest.raises(ValidationError):
        ApplicationCreate(company_id=uuid.uuid4(), job_title="C" * 256)


def test_application_create_invalid_status_rejected():
    """Verify invalid status is rejected by schema validator."""
    with pytest.raises(ValidationError) as exc_info:
        ApplicationCreate(
            company_id=uuid.uuid4(),
            job_title="Engineer",
            status="InvalidStatus",
        )
    assert any("Invalid status" in err["msg"] for err in exc_info.value.errors())


def test_application_create_invalid_employment_type_rejected():
    """Verify invalid employment_type is rejected by schema validator."""
    with pytest.raises(ValidationError) as exc_info:
        ApplicationCreate(
            company_id=uuid.uuid4(),
            job_title="Engineer",
            employment_type="InvalidType",
        )
    assert any("Invalid employment type" in err["msg"] for err in exc_info.value.errors())


def test_application_create_salary_validation():
    """Verify salary_min <= salary_max and non-negative constraints."""
    # Min > Max rejected
    with pytest.raises(ValidationError) as exc_info:
        ApplicationCreate(
            company_id=uuid.uuid4(),
            job_title="Engineer",
            salary_min=Decimal("150000"),
            salary_max=Decimal("120000"),
        )
    assert any("Minimum salary cannot exceed maximum salary" in err["msg"] for err in exc_info.value.errors())

    # Negative min rejected
    with pytest.raises(ValidationError):
        ApplicationCreate(
            company_id=uuid.uuid4(),
            job_title="Engineer",
            salary_min=Decimal("-100"),
        )

    # Negative max rejected
    with pytest.raises(ValidationError):
        ApplicationCreate(
            company_id=uuid.uuid4(),
            job_title="Engineer",
            salary_max=Decimal("-100"),
        )


def test_application_create_rejects_server_managed_fields():
    """Verify server-managed or forbidden fields are rejected via extra='forbid'."""
    forbidden = [
        {"user_id": str(uuid.uuid4())},
        {"id": str(uuid.uuid4())},
        {"is_archived": True},
        {"created_at": datetime.now(timezone.utc).isoformat()},
        {"updated_at": datetime.now(timezone.utc).isoformat()},
    ]
    base_payload = {"company_id": str(uuid.uuid4()), "job_title": "Engineer"}
    for f in forbidden:
        payload = {**base_payload, **f}
        with pytest.raises(ValidationError):
            ApplicationCreate(**payload)


def test_application_update_valid_partial():
    """Verify ApplicationUpdate allows valid partial updates."""
    # Only job_title
    u1 = ApplicationUpdate(job_title="  Lead Architect  ")
    assert u1.job_title == "Lead Architect"
    assert u1.company_id is None
    assert u1.location is None
    assert "job_title" in u1.model_fields_set
    assert "company_id" not in u1.model_fields_set

    # Only location and salary
    u2 = ApplicationUpdate(location="New York, NY", salary_min=Decimal("140000"))
    assert u2.location == "New York, NY"
    assert u2.salary_min == Decimal("140000")
    assert u2.job_title is None


def test_application_update_company_id_none_rejected_while_omitted_is_valid():
    """Verify explicit company_id=None is rejected while omitted company_id remains valid."""
    # Omitted company_id is valid
    u_omitted = ApplicationUpdate(job_title="Staff Engineer")
    assert u_omitted.company_id is None
    assert "company_id" not in u_omitted.model_fields_set

    # Empty ApplicationUpdate() is valid
    u_empty = ApplicationUpdate()
    assert u_empty.company_id is None
    assert "company_id" not in u_empty.model_fields_set

    # Valid company_id is accepted
    new_cid = uuid.uuid4()
    u_valid = ApplicationUpdate(company_id=new_cid)
    assert u_valid.company_id == new_cid
    assert "company_id" in u_valid.model_fields_set

    # Explicit company_id=None via constructor is rejected
    with pytest.raises(ValidationError) as exc_info:
        ApplicationUpdate(company_id=None)
    assert any("Company ID cannot be null" in str(err) for err in exc_info.value.errors())

    # Explicit company_id=None via model_validate is rejected
    with pytest.raises(ValidationError) as exc_info2:
        ApplicationUpdate.model_validate({"company_id": None})
    assert any("Company ID cannot be null" in str(err) for err in exc_info2.value.errors())


def test_application_update_rejects_status():
    """Verify ApplicationUpdate explicitly forbids status modification (reserved for Phase 4)."""
    with pytest.raises(ValidationError):
        ApplicationUpdate(status="Interview")


def test_application_update_rejects_is_archived():
    """Verify ApplicationUpdate rejects is_archived (archiving/unarchiving must remain separate operations)."""
    assert "is_archived" not in ApplicationUpdate.model_fields

    with pytest.raises(ValidationError):
        ApplicationUpdate(is_archived=True)

    with pytest.raises(ValidationError):
        ApplicationUpdate.model_validate({"is_archived": False})


def test_application_update_currency_validation():
    """Verify currency validation on ApplicationUpdate enforces 3 uppercase alphabetic chars."""
    # Valid currency
    u1 = ApplicationUpdate(currency="  cad  ")
    assert u1.currency == "CAD"
    assert "currency" in u1.model_fields_set

    # Omitted currency
    u_none = ApplicationUpdate()
    assert u_none.currency is None
    assert "currency" not in u_none.model_fields_set

    # Invalid currency rejected
    with pytest.raises(ValidationError):
        ApplicationUpdate(currency="US")

    with pytest.raises(ValidationError):
        ApplicationUpdate(currency="USDD")

    with pytest.raises(ValidationError):
        ApplicationUpdate(currency="US1")


def test_application_update_applied_at_validation():
    """Verify applied_at validation on ApplicationUpdate rejects naive datetimes."""
    # Aware datetime accepted
    aware_dt = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
    u = ApplicationUpdate(applied_at=aware_dt)
    assert u.applied_at == aware_dt

    # Naive datetime rejected
    naive_dt = datetime(2026, 3, 1, 12, 0, 0)
    with pytest.raises(ValidationError):
        ApplicationUpdate(applied_at=naive_dt)

    # Naive ISO string rejected
    with pytest.raises(ValidationError):
        ApplicationUpdate.model_validate({"applied_at": "2026-03-01T12:00:00"})


def test_application_update_job_title_none_rejected_while_omitted_is_valid():
    """Verify explicit job_title=None is rejected while omitted remains valid."""
    # Omitted is valid
    u_omitted = ApplicationUpdate()
    assert u_omitted.job_title is None
    assert "job_title" not in u_omitted.model_fields_set

    # Explicit None is rejected
    with pytest.raises(ValidationError) as exc_info:
        ApplicationUpdate(job_title=None)
    assert any("Job title cannot be null" in err["msg"] for err in exc_info.value.errors())


def test_application_update_blank_job_title_rejected():
    """Verify empty or whitespace-only job_title is rejected on update."""
    with pytest.raises(ValidationError):
        ApplicationUpdate(job_title="   ")


def test_application_update_clearing_optional_fields():
    """Verify optional fields can be explicitly cleared to None."""
    u = ApplicationUpdate(
        job_description=None,
        notes=None,
        job_url=None,
        location=None,
    )
    assert u.job_description is None
    assert "job_description" in u.model_fields_set
    assert "notes" in u.model_fields_set


def test_application_update_salary_partial_bounds():
    """Verify salary partial updates accept single bounds and validate min <= max when both provided."""
    # Only salary_min provided
    u_min = ApplicationUpdate(salary_min=Decimal("80000"))
    assert u_min.salary_min == Decimal("80000")
    assert u_min.salary_max is None

    # Only salary_max provided
    u_max = ApplicationUpdate(salary_max=Decimal("120000"))
    assert u_max.salary_max == Decimal("120000")
    assert u_max.salary_min is None

    # Both provided with min <= max
    u_both = ApplicationUpdate(salary_min=Decimal("80000"), salary_max=Decimal("120000"))
    assert u_both.salary_min == Decimal("80000")
    assert u_both.salary_max == Decimal("120000")

    # Both provided with min > max rejected
    with pytest.raises(ValidationError):
        ApplicationUpdate(salary_min=Decimal("130000"), salary_max=Decimal("100000"))

    # Negative values rejected
    with pytest.raises(ValidationError):
        ApplicationUpdate(salary_min=Decimal("-500"))


def test_application_update_rejects_server_managed_fields():
    """Verify user_id, id, etc., are rejected on update."""
    with pytest.raises(ValidationError):
        ApplicationUpdate(user_id=str(uuid.uuid4()))

    with pytest.raises(ValidationError):
        ApplicationUpdate(id=str(uuid.uuid4()))


def test_application_out_serialization_from_orm_with_company():
    """Verify ApplicationOut serializes properly from an ORM instance with loaded company."""
    app_id = uuid.uuid4()
    u_id = uuid.uuid4()
    c_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    comp = Company(
        id=c_id,
        user_id=u_id,
        name="Stripe Payments",
    )

    orm_app = Application(
        id=app_id,
        user_id=u_id,
        company_id=c_id,
        job_title="Site Reliability Engineer",
        job_description="Maintain cloud infra",
        job_url="https://example.com/jobs/sre",
        employment_type="Full-time",
        location="Austin, TX",
        salary_min=Decimal("125000.00"),
        salary_max=Decimal("155000.00"),
        currency="USD",
        status="Interview",
        applied_at=now,
        notes="First round completed.",
        is_archived=False,
        created_at=now,
        updated_at=now,
    )
    orm_app.company = comp

    out = ApplicationOut.model_validate(orm_app)
    assert out.id == app_id
    assert out.user_id == u_id
    assert out.company_id == c_id
    assert out.job_title == "Site Reliability Engineer"
    assert out.job_description == "Maintain cloud infra"
    assert out.job_url == "https://example.com/jobs/sre"
    assert out.employment_type == "Full-time"
    assert out.location == "Austin, TX"
    assert out.salary_min == Decimal("125000.00")
    assert out.salary_max == Decimal("155000.00")
    assert out.currency == "USD"
    assert out.status == "Interview"
    assert out.applied_at == now
    assert out.notes == "First round completed."
    assert out.is_archived is False
    assert out.created_at == now
    assert out.updated_at == now
    # Verified: company_name is populated from company relationship
    assert out.company_name == "Stripe Payments"


def test_application_out_serialization_without_company():
    """Verify ApplicationOut has company_name=None when company relationship is not loaded."""
    app_id = uuid.uuid4()
    u_id = uuid.uuid4()
    c_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    orm_app = Application(
        id=app_id,
        user_id=u_id,
        company_id=c_id,
        job_title="Software Engineer",
        status="Saved",
        is_archived=False,
        created_at=now,
        updated_at=now,
    )

    out = ApplicationOut.model_validate(orm_app)
    assert out.company_name is None


def test_application_out_serialization_from_dict():
    """Verify ApplicationOut can be constructed directly from a dict with company_name."""
    app_id = uuid.uuid4()
    u_id = uuid.uuid4()
    c_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    data = {
        "id": app_id,
        "user_id": u_id,
        "company_id": c_id,
        "job_title": "Frontend Engineer",
        "status": "Saved",
        "is_archived": False,
        "created_at": now,
        "updated_at": now,
        "company_name": "Google",
    }
    out = ApplicationOut.model_validate(data)
    assert out.company_name == "Google"

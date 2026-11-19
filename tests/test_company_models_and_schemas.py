"""Focused unit and database integration tests for Company SQLAlchemy model and Pydantic schemas.

Covers:
Model tests:
1. Company model defines all required columns with expected types, nullability, and defaults.
2. Company.user_id foreign key references users.id with ON DELETE CASCADE.
3. Composite unique constraint uq_companies_user_name_normalized on (user_id, name_normalized).
4. Covering index idx_companies_user_id_is_archived_name on (user_id, is_archived, name).
5. User.companies and Company.user bidirectional relationship configuration.
6. UUIDPrimaryKeyMixin and TimestampMixin inheritance and timezone-aware timestamps.
7. Model-level name and name_normalized synchronization via SQLAlchemy validators.
8. Model-level empty or whitespace-only name rejection.

Database constraint tests (PostgreSQL):
9. Creating and persisting a company with required and optional fields.
10. Same-user duplicate company name rejected by database unique constraint (case-insensitive).
11. Different users can create companies with the exact same name.
12. Archived company preserves uniqueness constraint for the same user.
13. Deleting a user cascades and deletes all associated company records.

Schema tests:
14. CompanyCreate validates valid payloads and normalizes whitespace.
15. CompanyCreate trims optional text fields and normalizes empty strings to None.
16. CompanyCreate rejects empty or whitespace-only company names.
17. CompanyCreate enforces name length boundaries (1-255 characters).
18. CompanyCreate rejects server-managed and ownership fields (user_id, id, name_normalized, etc.).
19. CompanyUpdate allows partial updates and validates field boundaries.
20. CompanyUpdate rejects empty or whitespace-only names when provided.
21. CompanyUpdate rejects explicit name=None while omitted name remains valid.
22. CompanyUpdate rejects server-managed and ownership fields.
23. CompanyOut serializes accurately from ORM instances and excludes internal fields.
"""

from datetime import datetime, timezone
from typing import Generator
import uuid

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import engine
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin
from app.models.company import Company
from app.models.user import User
from app.schemas.company import CompanyCreate, CompanyOut, CompanyUpdate


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


def _create_test_user(db: Session, email: str = "candidate@example.com") -> User:
    """Helper to persist an active test user for ownership foreign keys."""
    user = User(
        full_name="Candidate User",
        email=email,
        password_hash="$argon2id$v=19$m=65536,t=3,p=4$dummyhash",
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


# ============================================================================
# 1. SQLAlchemy Model Structure & Metadata Tests
# ============================================================================


def test_company_model_columns():
    """Verify Company model defines all required columns with expected types and nullability."""
    cols = Company.__table__.columns

    expected_columns = {
        "id",
        "user_id",
        "name",
        "name_normalized",
        "website",
        "industry",
        "location",
        "notes",
        "is_archived",
        "created_at",
        "updated_at",
    }
    assert expected_columns.issubset(set(cols.keys()))

    # Nullability assertions
    assert cols["id"].primary_key is True
    assert cols["user_id"].nullable is False
    assert cols["name"].nullable is False
    assert cols["name_normalized"].nullable is False
    assert cols["website"].nullable is True
    assert cols["industry"].nullable is True
    assert cols["location"].nullable is True
    assert cols["notes"].nullable is True
    assert cols["is_archived"].nullable is False
    assert cols["created_at"].nullable is False
    assert cols["updated_at"].nullable is False

    # Default values
    assert cols["is_archived"].default.arg is False


def test_company_foreign_key_and_cascade():
    """Verify Company.user_id foreign key references users.id with ON DELETE CASCADE."""
    cols = Company.__table__.columns
    user_id_col = cols["user_id"]

    assert len(user_id_col.foreign_keys) == 1
    fk = list(user_id_col.foreign_keys)[0]
    assert fk.target_fullname == "users.id"
    assert fk.ondelete.upper() == "CASCADE"


def test_company_unique_constraint_and_indexes():
    """Verify composite unique constraint and covering indexes on companies table."""
    table = Company.__table__

    # 1. Unique constraint: (user_id, name_normalized)
    uq_names = {uq.name: [c.name for c in uq.columns] for uq in table.constraints if hasattr(uq, "columns") and uq.name}
    assert "uq_companies_user_name_normalized" in uq_names
    assert uq_names["uq_companies_user_name_normalized"] == ["user_id", "name_normalized"]

    # 2. Covering index: (user_id, is_archived, name)
    index_names = {idx.name: [c.name for c in idx.columns] for idx in table.indexes}
    assert "idx_companies_user_id_is_archived_name" in index_names
    assert index_names["idx_companies_user_id_is_archived_name"] == ["user_id", "is_archived", "name"]


def test_company_user_relationship():
    """Verify User and Company bidirectional relationship configuration."""
    assert hasattr(User, "companies")
    assert hasattr(Company, "user")

    user_companies_rel = User.__mapper__.relationships["companies"]
    company_user_rel = Company.__mapper__.relationships["user"]

    assert user_companies_rel.target == Company.__table__
    assert company_user_rel.target == User.__table__
    assert user_companies_rel.back_populates == "user"
    assert company_user_rel.back_populates == "companies"
    assert "delete-orphan" in user_companies_rel.cascade


def test_company_uuid_and_timestamp_mixins_reused():
    """Verify Company model inherits from Base, UUIDPrimaryKeyMixin, and TimestampMixin."""
    assert issubclass(Company, UUIDPrimaryKeyMixin)
    assert issubclass(Company, TimestampMixin)


def test_company_model_name_and_normalized_synchronization():
    """Verify Company model automatically trims name and synchronizes name_normalized."""
    u_id = uuid.uuid4()
    company = Company(user_id=u_id, name="  Anthropic PBC  ")

    assert company.name == "Anthropic PBC"
    assert company.name_normalized == "anthropic pbc"

    # Updating name resynchronizes name_normalized
    company.name = "  Google DeepMind  "
    assert company.name == "Google DeepMind"
    assert company.name_normalized == "google deepmind"


def test_company_model_empty_name_rejected_by_validator():
    """Verify Company model rejects empty or whitespace-only names."""
    u_id = uuid.uuid4()
    with pytest.raises(ValueError, match="Company name cannot be empty or whitespace only."):
        Company(user_id=u_id, name="   ")

    with pytest.raises(ValueError, match="Company name cannot exceed 255 characters."):
        Company(user_id=u_id, name="A" * 256)


# ============================================================================
# 2. Database Constraint & Integrity Tests (PostgreSQL)
# ============================================================================


def test_company_persistence_and_defaults(db_session: Session):
    """Verify company can be inserted into PostgreSQL with expected defaults."""
    user = _create_test_user(db_session, "creator@example.com")

    company = Company(
        user_id=user.id,
        name="Acme Corporation",
        website="https://acme.example.com",
        industry="Technology",
        location="San Francisco, CA",
        notes="Applied via referral",
    )
    db_session.add(company)
    db_session.commit()
    db_session.refresh(company)

    assert isinstance(company.id, uuid.UUID)
    assert company.user_id == user.id
    assert company.name == "Acme Corporation"
    assert company.name_normalized == "acme corporation"
    assert company.website == "https://acme.example.com"
    assert company.industry == "Technology"
    assert company.location == "San Francisco, CA"
    assert company.notes == "Applied via referral"
    assert company.is_archived is False
    assert isinstance(company.created_at, datetime)
    assert isinstance(company.updated_at, datetime)
    assert company.created_at.tzinfo is not None


def test_same_user_duplicate_company_name_rejected_by_database_constraint(db_session: Session):
    """Verify same user cannot insert duplicate company names (case-insensitive) in PostgreSQL."""
    user = _create_test_user(db_session, "user1@example.com")

    company1 = Company(user_id=user.id, name="Stripe")
    db_session.add(company1)
    db_session.commit()

    # Attempt to insert identical company with different casing
    company2 = Company(user_id=user.id, name="  STRIPE  ")
    db_session.add(company2)

    with pytest.raises(IntegrityError) as exc_info:
        db_session.commit()

    assert "uq_companies_user_name_normalized" in str(exc_info.value).lower()
    db_session.rollback()


def test_different_users_can_create_same_company_name(db_session: Session):
    """Verify different users can create companies with the exact same name without conflict."""
    user1 = _create_test_user(db_session, "user_alpha@example.com")
    user2 = _create_test_user(db_session, "user_beta@example.com")

    company1 = Company(user_id=user1.id, name="Datadog")
    company2 = Company(user_id=user2.id, name="Datadog")

    db_session.add_all([company1, company2])
    db_session.commit()

    assert company1.id != company2.id
    assert company1.name == company2.name
    assert company1.user_id == user1.id
    assert company2.user_id == user2.id


def test_archived_company_name_uniqueness_enforced_for_same_user(db_session: Session):
    """Verify archived company still enforces name uniqueness for the same user per database design."""
    user = _create_test_user(db_session, "archiver@example.com")

    company1 = Company(user_id=user.id, name="Linear", is_archived=True)
    db_session.add(company1)
    db_session.commit()

    # Attempt creating active company with same normalized name
    company2 = Company(user_id=user.id, name="linear", is_archived=False)
    db_session.add(company2)

    with pytest.raises(IntegrityError):
        db_session.commit()

    db_session.rollback()


def test_cascade_delete_user_removes_owned_companies(db_session: Session):
    """Verify deleting a User record cascades and removes all owned companies."""
    user = _create_test_user(db_session, "cascade.test@example.com")

    comp1 = Company(user_id=user.id, name="Company One")
    comp2 = Company(user_id=user.id, name="Company Two")
    db_session.add_all([comp1, comp2])
    db_session.commit()

    # Delete parent user
    db_session.delete(user)
    db_session.commit()

    # Verify child company records were cascade-deleted
    stmt = select(Company).where(Company.user_id == user.id)
    remaining_companies = db_session.scalars(stmt).all()
    assert len(remaining_companies) == 0


# ============================================================================
# 3. Pydantic Schemas Tests
# ============================================================================


def test_company_create_valid_payload():
    """Verify CompanyCreate accepts valid payloads and normalizes whitespace."""
    payload = {
        "name": "  Microsoft  ",
        "website": "  https://microsoft.com  ",
        "industry": "  Software  ",
        "location": "  Redmond, WA  ",
        "notes": "  Direct message with recruiter  ",
    }
    schema = CompanyCreate(**payload)

    assert schema.name == "Microsoft"
    assert schema.website == "https://microsoft.com"
    assert schema.industry == "Software"
    assert schema.location == "Redmond, WA"
    assert schema.notes == "Direct message with recruiter"


def test_company_create_trims_optional_fields_and_converts_empty_to_none():
    """Verify CompanyCreate normalizes empty string optional fields to None."""
    payload = {
        "name": "Apple",
        "website": "   ",
        "industry": "",
        "location": "   ",
        "notes": None,
    }
    schema = CompanyCreate(**payload)

    assert schema.name == "Apple"
    assert schema.website is None
    assert schema.industry is None
    assert schema.location is None
    assert schema.notes is None


def test_company_create_blank_name_rejected():
    """Verify CompanyCreate rejects empty or whitespace-only name."""
    with pytest.raises(ValidationError) as exc_info:
        CompanyCreate(name="   ")

    errors = exc_info.value.errors()
    assert any("Company name cannot be empty" in err["msg"] for err in errors)


def test_company_create_name_length_boundaries():
    """Verify CompanyCreate validates min_length=1 and max_length=255."""
    # Min length (1 char accepted)
    c_min = CompanyCreate(name="A")
    assert c_min.name == "A"

    # Max length (255 chars accepted)
    c_max = CompanyCreate(name="B" * 255)
    assert len(c_max.name) == 255

    # Exceeding max length rejected
    with pytest.raises(ValidationError):
        CompanyCreate(name="C" * 256)


def test_company_create_rejects_server_managed_fields():
    """Verify CompanyCreate rejects ownership and server-managed fields via extra='forbid'."""
    forbidden_payloads = [
        {"name": "Valid Name", "user_id": str(uuid.uuid4())},
        {"name": "Valid Name", "id": str(uuid.uuid4())},
        {"name": "Valid Name", "name_normalized": "valid name"},
        {"name": "Valid Name", "is_archived": True},
        {"name": "Valid Name", "created_at": datetime.now(timezone.utc).isoformat()},
        {"name": "Valid Name", "updated_at": datetime.now(timezone.utc).isoformat()},
    ]

    for payload in forbidden_payloads:
        with pytest.raises(ValidationError):
            CompanyCreate(**payload)


def test_company_update_valid_partial_payload():
    """Verify CompanyUpdate supports partial field updates."""
    # Update only name
    update1 = CompanyUpdate(name="  OpenAI Inc  ")
    assert update1.name == "OpenAI Inc"
    assert update1.website is None
    assert update1.is_archived is None

    # Update only is_archived
    update2 = CompanyUpdate(is_archived=True)
    assert update2.is_archived is True
    assert update2.name is None


def test_company_update_blank_name_rejected():
    """Verify CompanyUpdate rejects empty or whitespace-only names when provided."""
    with pytest.raises(ValidationError):
        CompanyUpdate(name="   ")


def test_company_update_name_none_rejected_while_omitted_name_is_valid():
    """Verify CompanyUpdate rejects explicit name=None while omitted name remains valid."""
    # 1. CompanyUpdate() without name is valid (omitted name defaults to None)
    update_omitted = CompanyUpdate()
    assert update_omitted.name is None
    assert "name" not in update_omitted.model_fields_set

    # 2. CompanyUpdate with valid name and whitespace trimming
    update_valid = CompanyUpdate(name="  Google  ")
    assert update_valid.name == "Google"
    assert "name" in update_valid.model_fields_set

    # 3. Explicit name=None is rejected
    with pytest.raises(ValidationError) as exc_info:
        CompanyUpdate(name=None)
    errors = exc_info.value.errors()
    assert any("Company name cannot be null" in err["msg"] for err in errors)

    # 4. Explicit name=None via dictionary / model_validate is also rejected
    with pytest.raises(ValidationError) as exc_info_dict:
        CompanyUpdate.model_validate({"name": None})
    errors_dict = exc_info_dict.value.errors()
    assert any("Company name cannot be null" in err["msg"] for err in errors_dict)

    # 5. Other optional fields still allow explicit None (e.g., website=None)
    update_clear_website = CompanyUpdate(website=None)
    assert update_clear_website.website is None
    assert "website" in update_clear_website.model_fields_set


def test_company_update_rejects_server_managed_fields():
    """Verify CompanyUpdate rejects ownership and server-managed fields."""
    with pytest.raises(ValidationError):
        CompanyUpdate(user_id=str(uuid.uuid4()))

    with pytest.raises(ValidationError):
        CompanyUpdate(id=str(uuid.uuid4()))


def test_company_out_serialization_from_orm():
    """Verify CompanyOut serializes from an ORM Company instance and excludes name_normalized."""
    comp_id = uuid.uuid4()
    u_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    orm_company = Company(
        id=comp_id,
        user_id=u_id,
        name="GitHub",
        website="https://github.com",
        industry="Developer Tools",
        location="San Francisco, CA",
        notes="Git hosting platform",
        is_archived=False,
        created_at=now,
        updated_at=now,
    )

    out = CompanyOut.model_validate(orm_company)

    assert out.id == comp_id
    assert out.user_id == u_id
    assert out.name == "GitHub"
    assert out.website == "https://github.com"
    assert out.industry == "Developer Tools"
    assert out.location == "San Francisco, CA"
    assert out.notes == "Git hosting platform"
    assert out.is_archived is False
    assert out.created_at == now
    assert out.updated_at == now

    # Ensure name_normalized is not exposed in schema dict
    dumped = out.model_dump()
    assert "name_normalized" not in dumped

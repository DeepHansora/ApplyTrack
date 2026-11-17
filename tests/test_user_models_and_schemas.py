"""Focused unit tests for User and UserSession SQLAlchemy models and Pydantic schemas.

Covers:
Model tests:
1. User model contains all required columns and configurations.
2. UserSession model contains all required columns and configurations.
3. UserSession.user_id foreign key references users.id with ON DELETE CASCADE.
4. UserSession.token_hash has a unique constraint with no redundant indexes.
5. User.email has a unique constraint.
6. User and UserSession bidirectional relationship configuration.
7. UUID and timestamp mixins reuse and inheritance.
8. Model-level email and full name normalization via SQLAlchemy validators.

Schema tests:
1. Valid UserCreate payload validation.
2. UserCreate email normalization (trimming + lowercase).
3. UserCreate invalid email rejection.
4. UserCreate empty or whitespace-only full_name rejection.
5. UserCreate full_name length boundary checks (max 255 chars).
6. UserCreate password minimum length rejection (< 8 chars).
7. UserCreate password maximum length rejection (> 128 chars).
8. UserLogin validation and email normalization.
9. UserLogin password length boundaries.
10. UserOut serialization from SQLAlchemy User instance.
11. UserOut exclusion of password and password_hash.
"""

from datetime import datetime, timezone
import uuid

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import class_mapper

from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin
from app.models.user import User
from app.models.user_session import UserSession
from app.schemas.user import UserCreate, UserLogin, UserOut


# ============================================================================
# SQLAlchemy Model Tests
# ============================================================================


def test_user_model_columns():
    """Verify User model defines all required columns with expected types and nullability."""
    cols = User.__table__.columns

    # Required column presence
    expected_columns = {
        "id",
        "full_name",
        "email",
        "password_hash",
        "is_active",
        "ai_consent",
        "ai_consent_given_at",
        "created_at",
        "updated_at",
    }
    assert expected_columns.issubset(set(cols.keys()))

    # Nullability assertions
    assert cols["id"].primary_key is True
    assert cols["full_name"].nullable is False
    assert cols["email"].nullable is False
    assert cols["password_hash"].nullable is False
    assert cols["is_active"].nullable is False
    assert cols["ai_consent"].nullable is False
    assert cols["ai_consent_given_at"].nullable is True
    assert cols["created_at"].nullable is False
    assert cols["updated_at"].nullable is False

    # Default values
    assert cols["is_active"].default.arg is True
    assert cols["ai_consent"].default.arg is False


def test_user_session_model_columns():
    """Verify UserSession model defines all required columns with expected types and nullability."""
    cols = UserSession.__table__.columns

    expected_columns = {
        "id",
        "user_id",
        "token_hash",
        "expires_at",
        "created_at",
        "last_activity_at",
        "is_revoked",
    }
    assert expected_columns.issubset(set(cols.keys()))

    assert cols["id"].primary_key is True
    assert cols["user_id"].nullable is False
    assert cols["token_hash"].nullable is False
    assert cols["expires_at"].nullable is False
    assert cols["created_at"].nullable is False
    assert cols["last_activity_at"].nullable is False
    assert cols["is_revoked"].nullable is False

    assert cols["is_revoked"].default.arg is False


def test_user_session_foreign_key_and_cascade():
    """Verify UserSession.user_id references users.id with ON DELETE CASCADE."""
    user_id_col = UserSession.__table__.c.user_id
    foreign_keys = list(user_id_col.foreign_keys)

    assert len(foreign_keys) == 1
    fk = foreign_keys[0]
    assert fk.target_fullname == "users.id"
    assert fk.ondelete == "CASCADE"


def test_user_session_token_hash_unique_constraint():
    """Verify token_hash is strictly unique and does not create redundant indexes."""
    token_hash_col = UserSession.__table__.c.token_hash
    assert token_hash_col.unique is True

    # UserSession should have index on user_id, but no redundant standalone index on token_hash
    # (since the unique constraint already provides index lookup in standard RDBMS)
    index_column_sets = [
        set(col.name for col in idx.columns) for idx in UserSession.__table__.indexes
    ]
    assert {"user_id"} in index_column_sets
    assert {"token_hash"} not in index_column_sets


def test_user_email_unique_constraint():
    """Verify User.email has a database-level unique constraint."""
    email_col = User.__table__.c.email
    assert email_col.unique is True


def test_user_and_user_session_relationships():
    """Verify bidirectional relationship between User and UserSession with cascade."""
    user_mapper = class_mapper(User)
    session_mapper = class_mapper(UserSession)

    # User -> sessions
    assert "sessions" in user_mapper.relationships
    user_rel = user_mapper.relationships["sessions"]
    assert user_rel.entity.class_ is UserSession
    assert user_rel.back_populates == "user"
    assert "delete" in user_rel.cascade
    assert "delete-orphan" in user_rel.cascade

    # UserSession -> user
    assert "user" in session_mapper.relationships
    session_rel = session_mapper.relationships["user"]
    assert session_rel.entity.class_ is User
    assert session_rel.back_populates == "sessions"


def test_uuid_and_timestamp_mixins_reused():
    """Verify models properly inherit and reuse shared mixins."""
    assert issubclass(User, UUIDPrimaryKeyMixin)
    assert issubclass(User, TimestampMixin)
    assert issubclass(UserSession, UUIDPrimaryKeyMixin)

    # Verify column defaults on table metadata generate UUID v4
    assert User.__table__.c.id.default.is_callable is True
    user_id = User.__table__.c.id.default.arg(None)
    assert isinstance(user_id, uuid.UUID)
    assert user_id.version == 4

    assert UserSession.__table__.c.id.default.is_callable is True
    session_id = UserSession.__table__.c.id.default.arg(None)
    assert isinstance(session_id, uuid.UUID)
    assert session_id.version == 4

    assert callable(User.__table__.c.created_at.default.arg)
    assert callable(User.__table__.c.updated_at.default.arg)


def test_model_level_email_and_name_normalization():
    """Verify User model validates and normalizes email and full_name upon attribute assignment."""
    user = User(
        full_name="  Ada Lovelace  ",
        email="  Ada.Lovelace@ANALYSIS.ORG  ",
        password_hash="dummy_hash",
    )
    assert user.full_name == "Ada Lovelace"
    assert user.email == "ada.lovelace@analysis.org"

    # Verify repr excludes sensitive fields
    user_repr = repr(user)
    assert "dummy_hash" not in user_repr
    assert "ada.lovelace@analysis.org" in user_repr


# ============================================================================
# Pydantic Schema Tests
# ============================================================================


def test_valid_user_create_accepted():
    """Verify valid UserCreate data is accepted and parsed correctly."""
    data = {
        "full_name": "Alan Turing",
        "email": "alan.turing@enigma.org",
        "password": "CryptographicPassword123!",
        "ai_consent": True,
    }
    schema = UserCreate(**data)
    assert schema.full_name == "Alan Turing"
    assert schema.email == "alan.turing@enigma.org"
    assert schema.password == "CryptographicPassword123!"
    assert schema.ai_consent is True


def test_user_create_email_normalization():
    """Verify UserCreate trims whitespace and normalizes email to lowercase."""
    schema = UserCreate(
        full_name="Grace Hopper",
        email="  Grace.HOPPER@Navy.MIL  ",
        password="ValidPassword123",
    )
    assert schema.email == "grace.hopper@navy.mil"


def test_user_create_invalid_email_rejected():
    """Verify UserCreate rejects syntactically invalid emails."""
    invalid_emails = [
        "not-an-email",
        "grace@",
        "@navy.mil",
        "grace.hopper",
        "grace@navy..mil",
    ]
    for bad_email in invalid_emails:
        with pytest.raises(ValidationError):
            UserCreate(
                full_name="Grace Hopper",
                email=bad_email,
                password="ValidPassword123",
            )


def test_user_create_empty_or_whitespace_full_name_rejected():
    """Verify UserCreate rejects empty or whitespace-only full_name."""
    bad_names = ["", " ", "   ", "\t\n  "]
    for bad_name in bad_names:
        with pytest.raises(ValidationError):
            UserCreate(
                full_name=bad_name,
                email="valid@example.com",
                password="ValidPassword123",
            )


def test_user_create_full_name_length_boundaries():
    """Verify UserCreate enforces 1 to 255 character boundaries on full_name."""
    # 255 characters accepted
    valid_name = "A" * 255
    schema = UserCreate(
        full_name=valid_name,
        email="boundary@example.com",
        password="ValidPassword123",
    )
    assert len(schema.full_name) == 255

    # 256 characters rejected
    with pytest.raises(ValidationError):
        UserCreate(
            full_name="A" * 256,
            email="boundary@example.com",
            password="ValidPassword123",
        )


def test_user_create_password_length_boundaries():
    """Verify UserCreate enforces 8 to 128 character password constraints."""
    # 7 characters rejected (< 8)
    with pytest.raises(ValidationError):
        UserCreate(
            full_name="Short Pwd",
            email="short@example.com",
            password="Pass12!",
        )

    # 8 characters accepted
    schema_8 = UserCreate(
        full_name="Min Pwd",
        email="min@example.com",
        password="Password",
    )
    assert len(schema_8.password) == 8

    # 128 characters accepted
    pwd_128 = "A" * 128
    schema_128 = UserCreate(
        full_name="Max Pwd",
        email="max@example.com",
        password=pwd_128,
    )
    assert len(schema_128.password) == 128

    # 129 characters rejected (> 128)
    with pytest.raises(ValidationError):
        UserCreate(
            full_name="Too Long",
            email="toolong@example.com",
            password="A" * 129,
        )


def test_user_login_validates_and_normalizes_email():
    """Verify UserLogin validates email and normalizes casing/whitespace."""
    login = UserLogin(
        email="  LoginUser@DOMAIN.COM ",
        password="UserPassword123",
    )
    assert login.email == "loginuser@domain.com"
    assert login.password == "UserPassword123"

    # Invalid email rejected
    with pytest.raises(ValidationError):
        UserLogin(email="notanemail", password="UserPassword123")

    # Password length constraints (< 8 or > 128) enforced
    with pytest.raises(ValidationError):
        UserLogin(email="user@domain.com", password="short")

    with pytest.raises(ValidationError):
        UserLogin(email="user@domain.com", password="A" * 129)


def test_user_out_serializes_user_instance():
    """Verify UserOut serializes an ORM User instance using from_attributes=True."""
    user_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    user = User(
        id=user_id,
        full_name="Margaret Hamilton",
        email="margaret.hamilton@mit.edu",
        password_hash="argon2id_stored_hash_value",
        is_active=True,
        ai_consent=True,
        ai_consent_given_at=now,
        created_at=now,
        updated_at=now,
    )

    out = UserOut.model_validate(user)

    assert out.id == user_id
    assert out.full_name == "Margaret Hamilton"
    assert out.email == "margaret.hamilton@mit.edu"
    assert out.is_active is True
    assert out.ai_consent is True
    assert out.ai_consent_given_at == now
    assert out.created_at == now
    assert out.updated_at == now


def test_user_out_never_exposes_password_hash():
    """Verify UserOut schema fields and model dump strictly exclude password or password_hash."""
    # Ensure neither password nor password_hash is defined in UserOut model fields
    assert "password" not in UserOut.model_fields
    assert "password_hash" not in UserOut.model_fields

    now = datetime.now(timezone.utc)
    user = User(
        id=uuid.uuid4(),
        full_name="Dennis Ritchie",
        email="dennis@bell-labs.com",
        password_hash="$argon2id$v=19$m=65536,t=2,p=1$secret_hash_value",
        is_active=True,
        ai_consent=False,
        created_at=now,
        updated_at=now,
    )

    out = UserOut.model_validate(user)
    dumped = out.model_dump()

    assert "password" not in dumped
    assert "password_hash" not in dumped
    assert "secret_hash_value" not in str(dumped)

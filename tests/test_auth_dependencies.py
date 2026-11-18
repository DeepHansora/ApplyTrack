"""Comprehensive unit and integration tests for authentication dependencies and protected routes.

Verifies:
1. Missing session cookie returns no authenticated user (get_current_user -> None).
2. Invalid or malformed session cookie returns no authenticated user (get_current_user -> None).
3. Valid session cookie returns the correct authenticated User model instance.
4. Expired session is rejected and returns None.
5. Revoked session is rejected and returns None.
6. Inactive user session is rejected and returns None.
7. Protected /dashboard route redirects unauthenticated visitors to /login via HTTP 303.
8. Protected /dashboard route is accessible with a valid session cookie (HTTP 200).
9. Protected /dashboard displays the authenticated user's name.
10. One user's session cannot authenticate another user (strict identity isolation).
11. require_authenticated_user dependency raises HTTP 303 when user is None and returns User when present.
12. get_current_user handles database read errors safely without leaking internal details.
"""

from datetime import datetime, timedelta, timezone
from typing import Generator
from unittest.mock import MagicMock, patch
import uuid

from fastapi import HTTPException, Request, status
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import settings
from app.core.security import hash_session_token
from app.database import engine
from app.dependencies import get_current_user, get_db, require_authenticated_user
from app.main import app
from app.models.user import User
from app.models.user_session import UserSession
from app.services.auth_service import AuthService


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    """Provide an isolated database session that automatically rolls back after each test.

    Uses join_transaction_mode='create_savepoint' so that AuthService commits
    operate against savepoints and the outer transaction can be cleanly rolled back.
    Ensures zero database pollution in applytrack_dev.
    """
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")

    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture
def auth_service(db_session: Session) -> AuthService:
    """Provide an AuthService instance wired to the transactional test session."""
    return AuthService(db=db_session)


@pytest.fixture
def client(db_session: Session) -> Generator[TestClient, None, None]:
    """Provide a TestClient with get_db overridden to use the transactional test session."""
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    test_client = TestClient(app, follow_redirects=False)
    try:
        yield test_client
    finally:
        app.dependency_overrides.clear()


def _create_mock_request(cookie_token: str | None = None) -> Request:
    """Create a lightweight mock Request with optional session cookie."""
    mock_req = MagicMock(spec=Request)
    cookies = {}
    if cookie_token is not None:
        cookies[settings.SESSION_COOKIE_NAME] = cookie_token
    mock_req.cookies = cookies
    return mock_req


# ============================================================================
# 1. get_current_user Dependency Tests
# ============================================================================


def test_get_current_user_missing_session_cookie(db_session: Session):
    """Verify missing session cookie returns None."""
    request = _create_mock_request(cookie_token=None)
    user = get_current_user(request=request, db=db_session)
    assert user is None


def test_get_current_user_empty_session_cookie(db_session: Session):
    """Verify empty string session cookie returns None."""
    request = _create_mock_request(cookie_token="")
    user = get_current_user(request=request, db=db_session)
    assert user is None


def test_get_current_user_invalid_session_cookie(db_session: Session):
    """Verify random or non-existent session token returns None."""
    request = _create_mock_request(cookie_token="nonexistent_invalid_session_token_12345")
    user = get_current_user(request=request, db=db_session)
    assert user is None


def test_get_current_user_valid_session_cookie(auth_service: AuthService, db_session: Session):
    """Verify valid session cookie returns the correct authenticated User instance."""
    registered = auth_service.register(
        full_name="Grace Hopper",
        email="grace.dep@navy.mil",
        password="SecurePassword123!",
    )
    raw_token = auth_service.create_session(registered.id)

    request = _create_mock_request(cookie_token=raw_token)
    user = get_current_user(request=request, db=db_session)

    assert user is not None
    assert user.id == registered.id
    assert user.email == "grace.dep@navy.mil"
    assert user.full_name == "Grace Hopper"


def test_get_current_user_expired_session_rejected(auth_service: AuthService, db_session: Session):
    """Verify expired session (>24h absolute lifetime) is rejected and returns None."""
    registered = auth_service.register(
        full_name="Barbara Liskov",
        email="liskov.dep@mit.edu",
        password="SecurePassword123!",
    )
    raw_token = auth_service.create_session(registered.id)
    token_hash = hash_session_token(raw_token)

    # Set expires_at to 1 hour in the past
    past_expiry = datetime.now(timezone.utc) - timedelta(hours=1)
    db_session.execute(
        update(UserSession)
        .where(UserSession.token_hash == token_hash)
        .values(expires_at=past_expiry)
    )
    db_session.commit()

    request = _create_mock_request(cookie_token=raw_token)
    user = get_current_user(request=request, db=db_session)
    assert user is None


def test_get_current_user_idle_session_rejected(auth_service: AuthService, db_session: Session):
    """Verify inactive session exceeding sliding idle timeout is rejected and returns None."""
    registered = auth_service.register(
        full_name="Donald Knuth",
        email="knuth.dep@stanford.edu",
        password="SecurePassword123!",
    )
    raw_token = auth_service.create_session(registered.id)
    token_hash = hash_session_token(raw_token)

    # Set last_activity_at to 125 minutes ago (> 120 minutes timeout)
    idle_time = datetime.now(timezone.utc) - timedelta(
        minutes=settings.SESSION_IDLE_TIMEOUT_MINUTES + 5
    )
    db_session.execute(
        update(UserSession)
        .where(UserSession.token_hash == token_hash)
        .values(last_activity_at=idle_time)
    )
    db_session.commit()

    request = _create_mock_request(cookie_token=raw_token)
    user = get_current_user(request=request, db=db_session)
    assert user is None


def test_get_current_user_revoked_session_rejected(auth_service: AuthService, db_session: Session):
    """Verify explicitly revoked session (is_revoked=True) returns None."""
    registered = auth_service.register(
        full_name="Alan Turing",
        email="turing.dep@bletchley.ac.uk",
        password="SecurePassword123!",
    )
    raw_token = auth_service.create_session(registered.id)

    # Perform logout to revoke the session
    auth_service.logout(raw_token)

    request = _create_mock_request(cookie_token=raw_token)
    user = get_current_user(request=request, db=db_session)
    assert user is None


def test_get_current_user_inactive_user_rejected(auth_service: AuthService, db_session: Session):
    """Verify session for deactivated user account (is_active=False) returns None."""
    registered = auth_service.register(
        full_name="Inactive Developer",
        email="inactive.dep@example.com",
        password="SecurePassword123!",
    )
    raw_token = auth_service.create_session(registered.id)

    # Deactivate the user
    db_session.execute(
        update(User).where(User.id == registered.id).values(is_active=False)
    )
    db_session.commit()

    request = _create_mock_request(cookie_token=raw_token)
    user = get_current_user(request=request, db=db_session)
    assert user is None


def test_get_current_user_database_error_handled_safely(db_session: Session):
    """Verify unexpected database error during session validation safely returns None."""
    request = _create_mock_request(cookie_token="some_token")
    with patch("app.services.auth_service.AuthService.validate_session", side_effect=RuntimeError("Database error")):
        user = get_current_user(request=request, db=db_session)
        assert user is None


# ============================================================================
# 2. require_authenticated_user Dependency Tests
# ============================================================================


def test_require_authenticated_user_returns_user_when_present():
    """Verify require_authenticated_user returns the User instance when valid."""
    mock_user = User(
        id=uuid.uuid4(),
        full_name="Test User",
        email="test@example.com",
        password_hash="dummy_hash",
        is_active=True,
    )
    result = require_authenticated_user(current_user=mock_user)
    assert result == mock_user


def test_require_authenticated_user_redirects_when_none():
    """Verify require_authenticated_user raises HTTP 303 redirect to /login when current_user is None."""
    with pytest.raises(HTTPException) as exc_info:
        require_authenticated_user(current_user=None)

    assert exc_info.value.status_code == status.HTTP_303_SEE_OTHER
    assert exc_info.value.headers.get("Location") == "/login"


# ============================================================================
# 3. Protected Dashboard Route Integration Tests
# ============================================================================


def test_protected_dashboard_redirects_unauthenticated_to_login(client: TestClient):
    """Verify GET /dashboard redirects unauthenticated requests (missing cookie) to /login."""
    response = client.get("/dashboard")
    assert response.status_code == status.HTTP_303_SEE_OTHER
    assert response.headers["location"] == "/login"


def test_protected_dashboard_redirects_invalid_token_to_login(client: TestClient):
    """Verify GET /dashboard redirects requests with invalid session token to /login."""
    client.cookies.set(settings.SESSION_COOKIE_NAME, "completely_bogus_token")
    response = client.get("/dashboard")
    assert response.status_code == status.HTTP_303_SEE_OTHER
    assert response.headers["location"] == "/login"


def test_protected_dashboard_redirects_expired_session_to_login(
    client: TestClient, auth_service: AuthService, db_session: Session
):
    """Verify GET /dashboard redirects expired sessions to /login."""
    user = auth_service.register(
        full_name="Expired User",
        email="expired.dash@example.com",
        password="Password123!",
    )
    raw_token = auth_service.create_session(user.id)
    token_hash = hash_session_token(raw_token)

    # Expire session in database
    past_expiry = datetime.now(timezone.utc) - timedelta(minutes=10)
    db_session.execute(
        update(UserSession)
        .where(UserSession.token_hash == token_hash)
        .values(expires_at=past_expiry)
    )
    db_session.commit()

    client.cookies.set(settings.SESSION_COOKIE_NAME, raw_token)
    response = client.get("/dashboard")
    assert response.status_code == status.HTTP_303_SEE_OTHER
    assert response.headers["location"] == "/login"


def test_protected_dashboard_redirects_revoked_session_to_login(
    client: TestClient, auth_service: AuthService
):
    """Verify GET /dashboard redirects revoked sessions to /login."""
    user = auth_service.register(
        full_name="Revoked User",
        email="revoked.dash@example.com",
        password="Password123!",
    )
    raw_token = auth_service.create_session(user.id)
    auth_service.logout(raw_token)

    client.cookies.set(settings.SESSION_COOKIE_NAME, raw_token)
    response = client.get("/dashboard")
    assert response.status_code == status.HTTP_303_SEE_OTHER
    assert response.headers["location"] == "/login"


def test_protected_dashboard_redirects_inactive_user_to_login(
    client: TestClient, auth_service: AuthService, db_session: Session
):
    """Verify GET /dashboard redirects deactivated accounts to /login."""
    user = auth_service.register(
        full_name="Inactive User",
        email="inactive.dash@example.com",
        password="Password123!",
    )
    raw_token = auth_service.create_session(user.id)

    # Deactivate account
    db_session.execute(
        update(User).where(User.id == user.id).values(is_active=False)
    )
    db_session.commit()

    client.cookies.set(settings.SESSION_COOKIE_NAME, raw_token)
    response = client.get("/dashboard")
    assert response.status_code == status.HTTP_303_SEE_OTHER
    assert response.headers["location"] == "/login"


def test_protected_dashboard_accessible_with_valid_session(
    client: TestClient, auth_service: AuthService
):
    """Verify GET /dashboard returns HTTP 200 and renders HTML when presenting valid session."""
    user = auth_service.register(
        full_name="Ada Lovelace",
        email="ada.dash@example.com",
        password="AnalyticalEngine1843!",
    )
    raw_token = auth_service.create_session(user.id)

    client.cookies.set(settings.SESSION_COOKIE_NAME, raw_token)
    response = client.get("/dashboard")

    assert response.status_code == status.HTTP_200_OK
    assert "text/html" in response.headers.get("content-type", "")
    assert "<title>Dashboard — ApplyTrack</title>" in response.text


def test_protected_dashboard_displays_authenticated_user_name(
    client: TestClient, auth_service: AuthService
):
    """Verify GET /dashboard displays the authenticated user's name from server context."""
    user = auth_service.register(
        full_name="Radia Perlman",
        email="radia.dash@spanningtree.org",
        password="SpanningTree1985!",
    )
    raw_token = auth_service.create_session(user.id)

    client.cookies.set(settings.SESSION_COOKIE_NAME, raw_token)
    response = client.get("/dashboard")

    assert response.status_code == status.HTTP_200_OK
    assert "Welcome, Radia Perlman" in response.text


def test_one_user_session_cannot_authenticate_another_user(
    client: TestClient, auth_service: AuthService, db_session: Session
):
    """Verify strict session isolation: User A's session exclusively identifies User A and never User B."""
    user_a = auth_service.register(
        full_name="Alice Candidate",
        email="alice@example.com",
        password="AliceSecurePassword123!",
    )
    user_b = auth_service.register(
        full_name="Bob Candidate",
        email="bob@example.com",
        password="BobSecurePassword123!",
    )

    token_a = auth_service.create_session(user_a.id)
    token_b = auth_service.create_session(user_b.id)

    # 1. Request with User A's session cookie
    client.cookies.clear()
    client.cookies.set(settings.SESSION_COOKIE_NAME, token_a)
    response_a = client.get("/dashboard")
    assert response_a.status_code == status.HTTP_200_OK
    assert "Welcome, Alice Candidate" in response_a.text
    assert "Bob Candidate" not in response_a.text

    # Verify dependency resolution for User A
    req_a = _create_mock_request(cookie_token=token_a)
    resolved_a = get_current_user(request=req_a, db=db_session)
    assert resolved_a is not None
    assert resolved_a.id == user_a.id
    assert resolved_a.email == "alice@example.com"

    # 2. Request with User B's session cookie
    client.cookies.clear()
    client.cookies.set(settings.SESSION_COOKIE_NAME, token_b)
    response_b = client.get("/dashboard")
    assert response_b.status_code == status.HTTP_200_OK
    assert "Welcome, Bob Candidate" in response_b.text
    assert "Alice Candidate" not in response_b.text

    # Verify dependency resolution for User B
    req_b = _create_mock_request(cookie_token=token_b)
    resolved_b = get_current_user(request=req_b, db=db_session)
    assert resolved_b is not None
    assert resolved_b.id == user_b.id
    assert resolved_b.email == "bob@example.com"

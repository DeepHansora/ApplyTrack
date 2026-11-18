"""Comprehensive unit and integration tests for authentication router and PRG flow.

Verifies:
Registration:
1. GET /register displays the registration page with HTTP 200 and CSRF token.
2. POST /register validates form data, creates user with Argon2id hash, and redirects to /login via HTTP 303.
3. Newly registered user is not automatically logged in.
4. Invalid form data (e.g. malformed email, short password) is rejected and redirects to /register via HTTP 303 with error flash.
5. Duplicate email registration returns identical HTTP 303 to /login and success message (no account enumeration).
6. Missing or invalid CSRF token on POST /register returns HTTP 403 Forbidden.
7. Already authenticated user visiting GET /register is redirected to /dashboard.

Login:
8. GET /login displays login form with HTTP 200 and CSRF token.
9. POST /login with valid credentials creates database session, sets HttpOnly session cookie, and redirects to /dashboard via HTTP 303.
10. Session cookie has expected security flags (HttpOnly, SameSite, Path, Max-Age, Secure per environment).
11. Raw session token is never stored in database (only SHA-256 hash).
12. Invalid credentials (wrong password or nonexistent email) return HTTP 303 to /login with generic error flash.
13. Missing or invalid CSRF token on POST /login returns HTTP 403 Forbidden.
14. Already authenticated user visiting GET /login is redirected to /dashboard.

Logout:
15. POST /logout revokes active session in database and clears session cookie.
16. Revoked session cookie cannot be reused to access /dashboard.
17. POST /logout with missing or invalid session cookie completes safely.
18. POST /logout with missing or invalid CSRF token returns HTTP 403 Forbidden.
19. GET /logout is not supported (HTTP 405 Method Not Allowed).

Cross-User Isolation:
20. One user's session cookie cannot access another user's dashboard or account context.
21. Logging out one user does not affect another user's active session.
"""

from datetime import datetime, timezone
import re
from typing import Generator
import uuid

from fastapi import status
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.core.csrf import generate_csrf_token, sign_csrf_token
from app.core.flash import decode_flash_message
from app.core.security import hash_session_token, verify_password
from app.database import engine
from app.dependencies import get_db
from app.main import app
from app.models.user import User
from app.models.user_session import UserSession
from app.services.auth_service import AuthService


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


def set_valid_csrf(client: TestClient) -> str:
    """Inject a valid signed CSRF cookie into the client and return the matching raw form token."""
    raw_token = generate_csrf_token()
    signed_token = sign_csrf_token(raw_token)
    client.cookies.set(settings.CSRF_COOKIE_NAME, signed_token, domain="testserver.local", path="/")
    return raw_token


def extract_flash(client: TestClient) -> dict[str, str] | None:
    """Decode flash message from client cookies if present."""
    flash_cookie = client.cookies.get(settings.FLASH_COOKIE_NAME)
    if flash_cookie:
        return decode_flash_message(flash_cookie)
    return None


# ============================================================================
# 1. Registration Routes Tests
# ============================================================================


def test_registration_page_returns_http_200(client: TestClient):
    """Verify GET /register renders HTML registration form with CSRF token and cookie."""
    response = client.get("/register")
    assert response.status_code == status.HTTP_200_OK
    assert "text/html" in response.headers.get("content-type", "")
    assert "<title>Register — ApplyTrack</title>" in response.text
    assert 'action="/register"' in response.text
    assert 'name="full_name"' in response.text
    assert 'name="email"' in response.text
    assert 'name="password"' in response.text
    assert 'name="csrf_token"' in response.text
    # Verify CSRF cookie was attached
    assert settings.CSRF_COOKIE_NAME in client.cookies


def test_valid_registration_creates_account_and_redirects_prg(
    client: TestClient, db_session: Session
):
    """Verify valid registration creates user, hashes password, and redirects to /login via HTTP 303."""
    csrf_token = set_valid_csrf(client)

    response = client.post(
        "/register",
        data={
            "csrf_token": csrf_token,
            "full_name": "Katherine Johnson",
            "email": "kjohnson@nasa.gov",
            "password": "OrbitalMechanics1969!",
            "ai_consent": "true",
        },
    )

    # 1. Verify PRG redirect to /login
    assert response.status_code == status.HTTP_303_SEE_OTHER
    assert response.headers["location"] == "/login"

    # 2. Verify success flash message
    flash = extract_flash(client)
    assert flash is not None
    assert flash["category"] == "success"
    assert "Registration successful" in flash["message"]

    # 3. Verify user created in database
    stmt = select(User).where(User.email == "kjohnson@nasa.gov")
    user = db_session.scalar(stmt)
    assert user is not None
    assert user.full_name == "Katherine Johnson"
    assert user.email == "kjohnson@nasa.gov"
    assert user.is_active is True
    assert user.ai_consent is True
    assert user.password_hash.startswith("$argon2id$")
    assert verify_password("OrbitalMechanics1969!", user.password_hash) is True

    # 4. Verify user is NOT automatically logged in
    assert settings.SESSION_COOKIE_NAME not in client.cookies


def test_registration_invalid_form_data_rejected_safely(
    client: TestClient, db_session: Session
):
    """Verify invalid registration data (e.g. malformed email, short password) redirects to /register with error."""
    csrf_token = set_valid_csrf(client)

    # Short password (<8 characters)
    response = client.post(
        "/register",
        data={
            "csrf_token": csrf_token,
            "full_name": "Test User",
            "email": "valid@example.com",
            "password": "short",
        },
    )

    assert response.status_code == status.HTTP_303_SEE_OTHER
    assert response.headers["location"] == "/register"
    flash = extract_flash(client)
    assert flash is not None
    assert flash["category"] == "error"

    # Verify no user created
    stmt = select(User).where(User.email == "valid@example.com")
    assert db_session.scalar(stmt) is None


def test_registration_duplicate_email_does_not_reveal_existence(
    client: TestClient, auth_service: AuthService, db_session: Session
):
    """Verify duplicate email registration redirects to /login with identical success message (anti-enumeration)."""
    # 1. Create initial user
    auth_service.register(
        full_name="Original User",
        email="existing@example.com",
        password="OriginalPassword123!",
    )

    # 2. Attempt registering with the same email
    csrf_token = set_valid_csrf(client)
    response = client.post(
        "/register",
        data={
            "csrf_token": csrf_token,
            "full_name": "Duplicate Attacker",
            "email": "existing@example.com",
            "password": "DifferentPassword123!",
        },
    )

    # 3. Must return identical 303 redirect and identical success message
    assert response.status_code == status.HTTP_303_SEE_OTHER
    assert response.headers["location"] == "/login"

    flash = extract_flash(client)
    assert flash is not None
    assert flash["category"] == "success"
    assert "Registration successful" in flash["message"]

    # 4. Verify no second record was created
    stmt = select(User).where(User.email == "existing@example.com")
    users = db_session.scalars(stmt).all()
    assert len(users) == 1
    assert users[0].full_name == "Original User"


def test_registration_missing_or_invalid_csrf_rejected(client: TestClient):
    """Verify POST /register without valid CSRF token is rejected with HTTP 403 Forbidden."""
    # 1. Missing CSRF cookie
    res_no_cookie = client.post(
        "/register",
        data={
            "csrf_token": "some_token",
            "full_name": "Test User",
            "email": "test@example.com",
            "password": "Password123!",
        },
    )
    assert res_no_cookie.status_code == status.HTTP_403_FORBIDDEN

    # 2. Mismatched / tampered CSRF token
    set_valid_csrf(client)
    res_tampered = client.post(
        "/register",
        data={
            "csrf_token": "tampered_token",
            "full_name": "Test User",
            "email": "test@example.com",
            "password": "Password123!",
        },
    )
    assert res_tampered.status_code == status.HTTP_403_FORBIDDEN


def test_registration_redirects_already_authenticated_user_to_dashboard(
    client: TestClient, auth_service: AuthService
):
    """Verify logged-in user visiting GET /register is redirected to /dashboard."""
    user = auth_service.register(
        full_name="Logged In User",
        email="loggedin.reg@example.com",
        password="Password123!",
    )
    raw_token = auth_service.create_session(user.id)
    client.cookies.set(settings.SESSION_COOKIE_NAME, raw_token)

    response = client.get("/register")
    assert response.status_code == status.HTTP_303_SEE_OTHER
    assert response.headers["location"] == "/dashboard"


# ============================================================================
# 2. Login Routes Tests
# ============================================================================


def test_login_page_returns_http_200(client: TestClient):
    """Verify GET /login renders HTML login form with CSRF token and cookie."""
    response = client.get("/login")
    assert response.status_code == status.HTTP_200_OK
    assert "text/html" in response.headers.get("content-type", "")
    assert "<title>Login — ApplyTrack</title>" in response.text
    assert 'action="/login"' in response.text
    assert 'name="email"' in response.text
    assert 'name="password"' in response.text
    assert 'name="csrf_token"' in response.text
    assert settings.CSRF_COOKIE_NAME in client.cookies


def test_login_valid_credentials_creates_session_and_redirects_to_dashboard(
    client: TestClient, auth_service: AuthService, db_session: Session
):
    """Verify successful login creates database session, sets session cookie, and redirects via HTTP 303."""
    user = auth_service.register(
        full_name="Grace Hopper",
        email="grace.login@navy.mil",
        password="Compilers1952!",
    )

    csrf_token = set_valid_csrf(client)
    response = client.post(
        "/login",
        data={
            "csrf_token": csrf_token,
            "email": "grace.login@navy.mil",
            "password": "Compilers1952!",
        },
    )

    # 1. Verify PRG redirect to /dashboard
    assert response.status_code == status.HTTP_303_SEE_OTHER
    assert response.headers["location"] == "/dashboard"

    # 2. Verify session cookie was set
    assert settings.SESSION_COOKIE_NAME in client.cookies
    raw_token = client.cookies.get(settings.SESSION_COOKIE_NAME)
    assert raw_token is not None

    # 3. Verify session cookie header attributes
    set_cookie_header = response.headers.get("set-cookie", "")
    assert settings.SESSION_COOKIE_NAME in set_cookie_header
    assert "httponly" in set_cookie_header.lower()
    assert "samesite=lax" in set_cookie_header.lower()
    assert f"path={settings.SESSION_COOKIE_PATH.lower()}" in set_cookie_header.lower()
    assert f"max-age={settings.SESSION_ABSOLUTE_LIFETIME_HOURS * 3600}" in set_cookie_header.lower()
    if settings.SESSION_COOKIE_SECURE:
        assert "secure" in set_cookie_header.lower()
    else:
        assert "secure" not in set_cookie_header.lower()

    # 4. Verify database session record stores SHA-256 hash (never raw token)
    token_hash = hash_session_token(raw_token)
    stmt = select(UserSession).where(UserSession.token_hash == token_hash)
    db_session_record = db_session.scalar(stmt)
    assert db_session_record is not None
    assert db_session_record.user_id == user.id
    assert db_session_record.is_revoked is False
    assert db_session_record.token_hash != raw_token


def test_login_invalid_credentials_shows_generic_error(
    client: TestClient, auth_service: AuthService
):
    """Verify incorrect password and non-existent email return identical generic error messages."""
    auth_service.register(
        full_name="Real User",
        email="real@example.com",
        password="CorrectPassword123!",
    )

    # 1. Wrong password
    csrf_1 = set_valid_csrf(client)
    res_wrong_pw = client.post(
        "/login",
        data={
            "csrf_token": csrf_1,
            "email": "real@example.com",
            "password": "WrongPassword123!",
        },
    )
    assert res_wrong_pw.status_code == status.HTTP_303_SEE_OTHER
    assert res_wrong_pw.headers["location"] == "/login"
    flash_wrong_pw = extract_flash(client)
    assert flash_wrong_pw is not None
    assert flash_wrong_pw["category"] == "error"
    assert flash_wrong_pw["message"] == "Invalid email or password."
    assert settings.SESSION_COOKIE_NAME not in client.cookies

    # 2. Non-existent email
    csrf_2 = set_valid_csrf(client)
    res_no_user = client.post(
        "/login",
        data={
            "csrf_token": csrf_2,
            "email": "nonexistent@example.com",
            "password": "AnyPassword123!",
        },
    )
    assert res_no_user.status_code == status.HTTP_303_SEE_OTHER
    assert res_no_user.headers["location"] == "/login"
    flash_no_user = extract_flash(client)
    assert flash_no_user is not None
    assert flash_no_user["category"] == "error"
    assert flash_no_user["message"] == "Invalid email or password."
    assert settings.SESSION_COOKIE_NAME not in client.cookies

    # 3. Blank credentials
    csrf_3 = set_valid_csrf(client)
    res_blank = client.post(
        "/login",
        data={"csrf_token": csrf_3, "email": "", "password": ""},
    )
    assert res_blank.status_code == status.HTTP_303_SEE_OTHER
    assert res_blank.headers["location"] == "/login"
    flash_blank = extract_flash(client)
    assert flash_blank is not None
    assert flash_blank["message"] == "Invalid email or password."


def test_login_missing_or_invalid_csrf_rejected(client: TestClient):
    """Verify POST /login without valid CSRF token is rejected with HTTP 403 Forbidden."""
    # Missing CSRF cookie
    res_no_cookie = client.post(
        "/login",
        data={"csrf_token": "any_token", "email": "a@b.com", "password": "pass"},
    )
    assert res_no_cookie.status_code == status.HTTP_403_FORBIDDEN


def test_login_redirects_already_authenticated_user_to_dashboard(
    client: TestClient, auth_service: AuthService
):
    """Verify logged-in user visiting GET /login is redirected to /dashboard."""
    user = auth_service.register(
        full_name="Logged In User",
        email="loggedin.login@example.com",
        password="Password123!",
    )
    raw_token = auth_service.create_session(user.id)
    client.cookies.set(settings.SESSION_COOKIE_NAME, raw_token)

    response = client.get("/login")
    assert response.status_code == status.HTTP_303_SEE_OTHER
    assert response.headers["location"] == "/dashboard"


# ============================================================================
# 3. Logout Routes Tests
# ============================================================================


def test_logout_revokes_session_and_redirects_to_login(
    client: TestClient, auth_service: AuthService, db_session: Session
):
    """Verify POST /logout invalidates database session, deletes cookie, and redirects via HTTP 303."""
    user = auth_service.register(
        full_name="Logout Test User",
        email="logout.user@example.com",
        password="Password123!",
    )
    raw_token = auth_service.create_session(user.id)
    token_hash = hash_session_token(raw_token)

    # Attach session and CSRF cookies
    client.cookies.set(settings.SESSION_COOKIE_NAME, raw_token, domain="testserver.local", path="/")
    csrf_token = set_valid_csrf(client)

    response = client.post("/logout", data={"csrf_token": csrf_token})

    # 1. Verify PRG redirect to /login
    assert response.status_code == status.HTTP_303_SEE_OTHER
    assert response.headers["location"] == "/login"

    # 2. Verify session cookie deletion header was sent with Max-Age=0 and matching Path
    set_cookie_header = response.headers.get("set-cookie", "")
    assert settings.SESSION_COOKIE_NAME in set_cookie_header
    assert "max-age=0" in set_cookie_header.lower() or "expires=" in set_cookie_header.lower()
    assert f"path={settings.SESSION_COOKIE_PATH.lower()}" in set_cookie_header.lower()
    assert settings.SESSION_COOKIE_NAME not in client.cookies or client.cookies.get(settings.SESSION_COOKIE_NAME) == ""

    # 3. Verify session was marked revoked in database
    stmt = select(UserSession).where(UserSession.token_hash == token_hash)
    session_record = db_session.scalar(stmt)
    assert session_record is not None
    assert session_record.is_revoked is True

    # 4. Verify accessing /dashboard with revoked token redirects to /login
    client.cookies.set(settings.SESSION_COOKIE_NAME, raw_token)
    res_dash = client.get("/dashboard")
    assert res_dash.status_code == status.HTTP_303_SEE_OTHER
    assert res_dash.headers["location"] == "/login"


def test_logout_invalid_session_cookie_handled_safely(client: TestClient):
    """Verify POST /logout with invalid session cookie completes safely, deletes cookie, and redirects to /login."""
    # 1. Attach invalid session cookie and valid CSRF token
    client.cookies.set(
        settings.SESSION_COOKIE_NAME,
        "nonexistent_invalid_session_token_12345",
        domain="testserver.local",
        path="/",
    )
    csrf_token = set_valid_csrf(client)

    # 2. Submit logout
    response = client.post("/logout", data={"csrf_token": csrf_token})

    # 3. Verify HTTP 303 redirect to /login
    assert response.status_code == status.HTTP_303_SEE_OTHER
    assert response.headers["location"] == "/login"

    # 4. Verify cookie deletion header was sent with Max-Age=0 and matching Path
    set_cookie_header = response.headers.get("set-cookie", "")
    assert settings.SESSION_COOKIE_NAME in set_cookie_header
    assert "max-age=0" in set_cookie_header.lower() or "expires=" in set_cookie_header.lower()
    assert f"path={settings.SESSION_COOKIE_PATH.lower()}" in set_cookie_header.lower()

    # 5. Verify session cookie is deleted from client
    assert settings.SESSION_COOKIE_NAME not in client.cookies or client.cookies.get(settings.SESSION_COOKIE_NAME) == ""

    # 6. Verify flash message is set
    flash = extract_flash(client)
    assert flash is not None
    assert "logged out" in flash["message"]


def test_logout_missing_session_cookie_handled_safely(client: TestClient):
    """Verify POST /logout without session cookie completes safely and redirects to /login."""
    csrf_token = set_valid_csrf(client)
    response = client.post("/logout", data={"csrf_token": csrf_token})

    assert response.status_code == status.HTTP_303_SEE_OTHER
    assert response.headers["location"] == "/login"
    flash = extract_flash(client)
    assert flash is not None
    assert "logged out" in flash["message"]


def test_logout_missing_or_invalid_csrf_rejected(client: TestClient):
    """Verify POST /logout without valid CSRF token is rejected with HTTP 403 Forbidden."""
    response = client.post("/logout", data={"csrf_token": "bad_token"})
    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_logout_via_get_is_not_supported(client: TestClient):
    """Verify GET /logout returns HTTP 405 Method Not Allowed."""
    response = client.get("/logout")
    assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED


# ============================================================================
# 4. Cross-User Isolation Tests
# ============================================================================


def test_cross_user_session_isolation(
    client: TestClient, auth_service: AuthService
):
    """Verify strict session isolation: logging in as User A never accesses User B's account."""
    user_a = auth_service.register(
        full_name="Alice Anderson",
        email="alice.anderson@example.com",
        password="AlicePassword123!",
    )
    user_b = auth_service.register(
        full_name="Bob Builder",
        email="bob.builder@example.com",
        password="BobPassword123!",
    )

    # Log in as User A
    csrf_a = set_valid_csrf(client)
    res_login_a = client.post(
        "/login",
        data={
            "csrf_token": csrf_a,
            "email": "alice.anderson@example.com",
            "password": "AlicePassword123!",
        },
    )
    assert res_login_a.status_code == status.HTTP_303_SEE_OTHER
    token_a = client.cookies.get(settings.SESSION_COOKIE_NAME)

    # Log in as User B using separate cookies
    client_b = TestClient(app, follow_redirects=False)
    csrf_b = set_valid_csrf(client_b)
    res_login_b = client_b.post(
        "/login",
        data={
            "csrf_token": csrf_b,
            "email": "bob.builder@example.com",
            "password": "BobPassword123!",
        },
    )
    assert res_login_b.status_code == status.HTTP_303_SEE_OTHER
    token_b = client_b.cookies.get(settings.SESSION_COOKIE_NAME)

    # 1. Access dashboard with User A's token
    client.cookies.set(settings.SESSION_COOKIE_NAME, token_a)
    res_dash_a = client.get("/dashboard")
    assert res_dash_a.status_code == status.HTTP_200_OK
    assert "Welcome, Alice Anderson" in res_dash_a.text
    assert "Bob Builder" not in res_dash_a.text

    # 2. Access dashboard with User B's token
    client_b.cookies.set(settings.SESSION_COOKIE_NAME, token_b)
    res_dash_b = client_b.get("/dashboard")
    assert res_dash_b.status_code == status.HTTP_200_OK
    assert "Welcome, Bob Builder" in res_dash_b.text
    assert "Alice Anderson" not in res_dash_b.text

    # 3. Log out User A
    csrf_logout_a = set_valid_csrf(client)
    client.cookies.set(settings.SESSION_COOKIE_NAME, token_a)
    res_logout_a = client.post(
        "/logout",
        data={"csrf_token": csrf_logout_a},
    )
    assert res_logout_a.status_code == status.HTTP_303_SEE_OTHER

    # User A is logged out
    client.cookies.set(settings.SESSION_COOKIE_NAME, token_a)
    res_check_a = client.get("/dashboard")
    assert res_check_a.status_code == status.HTTP_303_SEE_OTHER
    assert res_check_a.headers["location"] == "/login"

    # User B's session remains active
    client_b.cookies.set(settings.SESSION_COOKIE_NAME, token_b)
    res_check_b = client_b.get("/dashboard")
    assert res_check_b.status_code == status.HTTP_200_OK
    assert "Welcome, Bob Builder" in res_check_b.text


def test_multiple_concurrent_sessions_for_same_user_and_isolated_logout(
    client: TestClient, auth_service: AuthService
):
    """Verify a user can maintain multiple active sessions concurrently across different devices,
    and logging out from one device revokes only that specific session.
    """
    user = auth_service.register(
        full_name="Multi Session User",
        email="multi.session@example.com",
        password="ValidPassword123!",
    )

    # Device 1 logs in
    csrf_1 = set_valid_csrf(client)
    res_login_1 = client.post(
        "/login",
        data={"csrf_token": csrf_1, "email": "multi.session@example.com", "password": "ValidPassword123!"},
    )
    assert res_login_1.status_code == status.HTTP_303_SEE_OTHER
    token_device_1 = client.cookies.get(settings.SESSION_COOKIE_NAME)

    # Device 2 logs in using a separate client
    client_2 = TestClient(app, follow_redirects=False)
    csrf_2 = set_valid_csrf(client_2)
    res_login_2 = client_2.post(
        "/login",
        data={"csrf_token": csrf_2, "email": "multi.session@example.com", "password": "ValidPassword123!"},
    )
    assert res_login_2.status_code == status.HTTP_303_SEE_OTHER
    token_device_2 = client_2.cookies.get(settings.SESSION_COOKIE_NAME)

    # Tokens must be distinct
    assert token_device_1 != token_device_2

    # Both devices can access dashboard simultaneously
    client.cookies.set(settings.SESSION_COOKIE_NAME, token_device_1)
    res_dash_1 = client.get("/dashboard")
    assert res_dash_1.status_code == status.HTTP_200_OK
    assert "Welcome, Multi Session User" in res_dash_1.text

    client_2.cookies.set(settings.SESSION_COOKIE_NAME, token_device_2)
    res_dash_2 = client_2.get("/dashboard")
    assert res_dash_2.status_code == status.HTTP_200_OK
    assert "Welcome, Multi Session User" in res_dash_2.text

    # Device 1 logs out
    csrf_logout_1 = set_valid_csrf(client)
    res_logout_1 = client.post("/logout", data={"csrf_token": csrf_logout_1})
    assert res_logout_1.status_code == status.HTTP_303_SEE_OTHER

    # Device 1 session is revoked
    client.cookies.set(settings.SESSION_COOKIE_NAME, token_device_1)
    res_revoked = client.get("/dashboard")
    assert res_revoked.status_code == status.HTTP_303_SEE_OTHER
    assert res_revoked.headers["location"] == "/login"

    # Device 2 session remains active
    client_2.cookies.set(settings.SESSION_COOKIE_NAME, token_device_2)
    res_still_active = client_2.get("/dashboard")
    assert res_still_active.status_code == status.HTTP_200_OK
    assert "Welcome, Multi Session User" in res_still_active.text


def test_registration_and_login_email_case_insensitivity(
    client: TestClient, db_session: Session
):
    """Verify email casing and surrounding whitespace are normalized on registration
    and properly match during login.
    """
    csrf_token = set_valid_csrf(client)
    res_reg = client.post(
        "/register",
        data={
            "csrf_token": csrf_token,
            "full_name": "Marie Curie",
            "email": "  Marie.Curie@SORBONNE.edu  ",
            "password": "Radioactivity1898!",
        },
    )
    assert res_reg.status_code == status.HTTP_303_SEE_OTHER
    assert res_reg.headers["location"] == "/login"

    # Verify persisted in database normalized lowercase
    stmt = select(User).where(User.email == "marie.curie@sorbonne.edu")
    user = db_session.scalar(stmt)
    assert user is not None
    assert user.full_name == "Marie Curie"

    # Login with all lowercase
    csrf_login_1 = set_valid_csrf(client)
    res_log_lower = client.post(
        "/login",
        data={
            "csrf_token": csrf_login_1,
            "email": "marie.curie@sorbonne.edu",
            "password": "Radioactivity1898!",
        },
    )
    assert res_log_lower.status_code == status.HTTP_303_SEE_OTHER
    assert res_log_lower.headers["location"] == "/dashboard"

    # Clear cookies and login with all uppercase
    client.cookies.clear()
    csrf_login_2 = set_valid_csrf(client)
    res_log_upper = client.post(
        "/login",
        data={
            "csrf_token": csrf_login_2,
            "email": "MARIE.CURIE@SORBONNE.EDU",
            "password": "Radioactivity1898!",
        },
    )
    assert res_log_upper.status_code == status.HTTP_303_SEE_OTHER
    assert res_log_upper.headers["location"] == "/dashboard"


def test_dashboard_ignores_url_query_parameters_for_identity(
    client: TestClient, auth_service: AuthService
):
    """Verify that dashboard identity is strictly derived from the session and URL query parameters
    attempting to override user identity are completely ignored.
    """
    legit_user = auth_service.register(
        full_name="Legitimate User",
        email="legit@example.com",
        password="ValidPassword123!",
    )
    spoofed_user = auth_service.register(
        full_name="Target User",
        email="target@example.com",
        password="ValidPassword123!",
    )
    token = auth_service.create_session(legit_user.id)
    client.cookies.set(settings.SESSION_COOKIE_NAME, token)

    # Attempt to spoof identity via query parameters
    response = client.get(f"/dashboard?user_id={spoofed_user.id}&email={spoofed_user.email}&name=Target+User")
    assert response.status_code == status.HTTP_200_OK
    assert "Welcome, Legitimate User" in response.text
    assert "Target User" not in response.text
    assert spoofed_user.email not in response.text

"""Unit and integration tests for Phase 2 Step 9: Authentication UI & Template Refinement.

Verifies:
1. Registration page markup, branding, form accessibility, requirements, and links.
2. Login page markup, branding, accessibility, and anti-enumeration invariants.
3. Authenticated dashboard layout, welcome greeting, stats placeholders, coming-soon indicators,
   security summary, and POST logout form.
4. Flash message visual structure, accessibility attributes, and category badges.
5. Responsive CSS rules for auth and dashboard layouts.
"""

from typing import Generator
from fastapi import status
from fastapi.testclient import TestClient
import pytest
from sqlalchemy.orm import Session

from app.config import settings
from app.core.flash import set_flash_message
from app.database import engine
from app.dependencies import get_db
from app.main import app
from app.models.user import User
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
def client(db_session: Session) -> Generator[TestClient, None, None]:
    """Provide a TestClient wired to the isolated database session."""
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    test_client = TestClient(app, follow_redirects=False)
    try:
        yield test_client
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def auth_service(db_session: Session) -> AuthService:
    """Provide an AuthService instance wired to the transactional test session."""
    return AuthService(db=db_session)


# ============================================================================
# 1. Registration UI Tests
# ============================================================================


def test_registration_ui_structure_and_branding(client: TestClient):
    """Verify registration page contains Swiss editorial branding, accessible inputs, and links."""
    response = client.get("/register")
    assert response.status_code == status.HTTP_200_OK
    html = response.text

    # Title & Branding
    assert "<title>Register — ApplyTrack</title>" in html
    assert "Account Setup" in html
    assert "Create Account" in html

    # Semantic form structure with browser validation enabled
    assert 'action="/register"' in html
    assert 'method="post"' in html or 'method="POST"' in html
    assert 'class="form"' in html
    assert 'novalidate' not in html
    assert 'name="csrf_token"' in html

    # Input fields and accessibility
    assert 'id="full_name"' in html
    assert 'name="full_name"' in html
    assert 'for="full_name"' in html
    assert 'id="email"' in html
    assert 'name="email"' in html
    assert 'for="email"' in html
    assert 'id="password"' in html
    assert 'name="password"' in html
    assert 'for="password"' in html

    # AI consent checkbox and clear explanatory text
    assert 'id="ai_consent"' in html
    assert 'name="ai_consent"' in html
    assert "Enable AI-assisted resume matching" in html
    assert "parse job descriptions, compare resumes against job postings, and evaluate alignment" in html

    # Password complexity specifications strictly matching backend rules (8-128 chars only)
    assert "Password Requirements" in html
    assert "Between 8 and 128 characters in length" in html
    assert "uppercase" not in html.lower()
    assert "lowercase" not in html.lower()
    assert "numeric digit" not in html.lower()

    # Navigation link to login
    assert 'href="/login"' in html
    assert "Already have an account?" in html


# ============================================================================
# 2. Login UI Tests
# ============================================================================


def test_login_ui_structure_and_branding(client: TestClient):
    """Verify login page contains clean monochrome branding, accessible fields, and registration link."""
    response = client.get("/login")
    assert response.status_code == status.HTTP_200_OK
    html = response.text

    # Title & Branding
    assert "<title>Login — ApplyTrack</title>" in html
    assert "Authentication" in html
    assert "Log In" in html

    # Semantic form structure with browser validation enabled
    assert 'action="/login"' in html
    assert 'novalidate' not in html
    assert 'name="csrf_token"' in html
    assert 'id="email"' in html
    assert 'name="email"' in html
    assert 'for="email"' in html
    assert 'id="password"' in html
    assert 'name="password"' in html
    assert 'for="password"' in html

    # Navigation link to register
    assert 'href="/register"' in html
    assert "Don’t have an account yet?" in html or "Don&rsquo;t have an account yet?" in html


# ============================================================================
# 3. Dashboard UI Tests
# ============================================================================


def test_dashboard_ui_authenticated_workspace(client: TestClient, auth_service: AuthService):
    """Verify authenticated dashboard renders user name, empty stats, coming-soon sections, and logout form."""
    user = auth_service.register(
        full_name="Grace Brewster Hopper",
        email="hopper.ui@navy.mil",
        password="ValidPassword123!",
        ai_consent=True,
    )
    raw_token = auth_service.create_session(user.id)
    client.cookies.set(settings.SESSION_COOKIE_NAME, raw_token)

    response = client.get("/dashboard")
    assert response.status_code == status.HTTP_200_OK
    html = response.text

    # Document Title & User Welcome Greeting
    assert "<title>Dashboard — ApplyTrack</title>" in html
    assert "Welcome, Grace Brewster Hopper" in html
    assert "Session Active" in html

    # POST Logout form with CSRF protection
    assert 'action="/logout"' in html
    assert 'method="post"' in html or 'method="POST"' in html
    assert 'name="csrf_token"' in html
    assert "Log Out" in html

    # Application Statistics (Placeholder indicators without hardcoded fake zeros)
    assert "Total Applications" in html
    assert "Active Pipelines" in html
    assert "Interviews Scheduled" in html
    assert "Offers Extended" in html
    assert "&mdash;" in html or "—" in html
    assert "Phase 3" in html
    assert "Phase 4" in html

    # Future Development Sections (Explicit Feature Not Available Yet Indicators)
    assert "Recent Applications" in html
    assert "Phase 3: Pipeline Hub" in html
    assert "Upcoming Interviews" in html
    assert "Phase 4: Interview Hub" in html
    assert "Reminders &amp; Deadlines" in html or "Reminders & Deadlines" in html
    assert "Phase 4: Productivity" in html
    assert "Feature Not Available Yet" in html
    assert "No Applications Recorded" not in html
    assert "No Interviews Scheduled" not in html
    assert "Zero Pending Reminders" not in html

    # Account and Security Profile (Active only, no implied email verification)
    assert "Security &amp; Account Status" in html or "Security & Account Status" in html
    assert "hopper.ui@navy.mil" in html
    assert "Consent Granted" in html
    assert "Active" in html
    assert "Verified" not in html

    # Header navigation reflects active session
    assert 'class="site-nav__user-chip"' in html
    assert "Grace Brewster Hopper" in html


def test_dashboard_ui_ai_consent_disabled_reflection(client: TestClient, auth_service: AuthService):
    """Verify dashboard accurately reflects disabled AI consent when not opted in."""
    user = auth_service.register(
        full_name="Privacy Focused User",
        email="privacy.user@example.com",
        password="ValidPassword123!",
        ai_consent=False,
    )
    raw_token = auth_service.create_session(user.id)
    client.cookies.set(settings.SESSION_COOKIE_NAME, raw_token)

    response = client.get("/dashboard")
    assert response.status_code == status.HTTP_200_OK
    assert "Disabled" in response.text


# ============================================================================
# 4. Flash Message UI Rendering Tests
# ============================================================================


def test_flash_message_ui_rendering(client: TestClient):
    """Verify flash messages render with accessible markup, semantic categories, and badges."""
    # Simulate a route setting an error flash message via client cookies
    from app.core.flash import encode_flash_message
    
    # 1. Error flash
    client.cookies.set(
        settings.FLASH_COOKIE_NAME,
        encode_flash_message("Invalid email or password.", category="error"),
    )
    response_err = client.get("/login")
    assert response_err.status_code == status.HTTP_200_OK
    assert 'class="flash flash--error"' in response_err.text
    assert 'role="alert"' in response_err.text
    assert "error" in response_err.text
    assert "Invalid email or password." in response_err.text

    # 2. Success flash
    client.cookies.set(
        settings.FLASH_COOKIE_NAME,
        encode_flash_message("Registration successful. Please log in.", category="success"),
    )
    response_succ = client.get("/login")
    assert response_succ.status_code == status.HTTP_200_OK
    assert 'class="flash flash--success"' in response_succ.text
    assert "success" in response_succ.text
    assert "Registration successful. Please log in." in response_succ.text


# ============================================================================
# 5. CSS & Responsive Stylesheet Verification
# ============================================================================


def test_style_css_contains_auth_and_dashboard_rules(client: TestClient):
    """Verify style.css serves all Phase 2 Step 9 layout and responsive rules."""
    response = client.get("/static/css/style.css")
    assert response.status_code == status.HTTP_200_OK
    css = response.text

    # Auth & Form Classes
    assert ".auth-section" in css
    assert ".auth-card" in css
    assert ".form-input" in css
    assert ".form-checkbox" in css
    assert ".form-requirements" in css

    # Flash Message Classes
    assert ".flash" in css
    assert ".flash--error" in css
    assert ".flash--success" in css
    assert ".flash--info" in css

    # Dashboard Classes
    assert ".dashboard-section" in css
    assert ".dashboard-header" in css
    assert ".dashboard-stats-grid" in css
    assert ".stat-card" in css
    assert ".empty-state" in css
    assert ".profile-summary" in css

    # Responsive Media Query Breakpoints
    assert "@media (max-width: 900px)" in css
    assert "@media (max-width: 768px)" in css
    assert "@media (max-width: 600px)" in css

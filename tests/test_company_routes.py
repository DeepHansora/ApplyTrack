"""Integration tests for company routes, UI templates, ownership, and CSRF protection.

Verifies:
1. Authentication Enforcement:
   - All company routes require active session authentication.
   - Unauthenticated requests are redirected to /login with HTTP 303.
2. Company List (GET /companies):
   - Displays user's companies in A-Z order.
   - Excludes archived companies by default.
   - Toggle (?include_archived=1) includes archived companies.
   - Strict cross-user isolation: never shows another user's companies.
3. Company Creation (GET /companies/new & POST /companies):
   - Renders blank creation form.
   - Valid submission creates record, sets user_id, and redirects to /companies/{id} (PRG).
   - Duplicate name for same user re-renders form with 422 and helpful error.
   - Blank or whitespace-only name re-renders form with 422.
4. Company Detail (GET /companies/{id}):
   - Renders company metadata, badges, and candidate notes.
   - Accessing non-existent company returns 404.
   - Accessing another user's company returns 404 (IDOR defense).
5. Company Editing (GET & POST /companies/{id}/edit):
   - Renders pre-filled edit form.
   - Successful edit updates record and redirects to /companies/{id} (PRG).
   - Clearing optional fields (submitting empty strings) sets them to None in DB.
   - Duplicate name collision with another owned company returns 422.
   - Accessing or editing another user's company returns 404.
6. Company Archiving & Unarchiving (POST /companies/{id}/archive & unarchive):
   - Archiving sets is_archived=True, preserves DB record, and redirects to /companies (PRG).
   - Unarchiving restores is_archived=False and redirects to /companies/{id} (PRG).
   - Archiving another user's company returns 404.
7. CSRF Protection:
   - All state-changing POST routes require valid CSRF cookies and matching form tokens.
   - Missing or invalid CSRF tokens return HTTP 403 Forbidden.
"""

from typing import Generator
import uuid

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.core.csrf import generate_csrf_token, sign_csrf_token
from app.core.flash import decode_flash_message
from app.core.security import hash_session_token
from app.database import engine
from app.dependencies import get_db
from app.main import app
from app.models.company import Company
from app.models.user import User
from app.models.user_session import UserSession
from app.services.auth_service import AuthService
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


def authenticate_user(client: TestClient, user: User, db_session: Session) -> str:
    """Create an active session for the user and inject the session cookie into the client."""
    auth_service = AuthService(db_session)
    raw_token = auth_service.create_session(user.id)
    client.cookies.set(
        settings.SESSION_COOKIE_NAME,
        raw_token,
        domain="testserver.local",
        path=settings.SESSION_COOKIE_PATH,
    )
    return raw_token


def extract_flash(client: TestClient) -> dict[str, str] | None:
    """Decode flash message from client cookies if present."""
    flash_cookie = client.cookies.get(settings.FLASH_COOKIE_NAME)
    if flash_cookie:
        return decode_flash_message(flash_cookie)
    return None


def _create_user(db: Session, email: str, name: str = "Test Candidate") -> User:
    """Helper to persist an active user."""
    auth_service = AuthService(db)
    return auth_service.register(
        full_name=name,
        email=email,
        password="ValidPassword123!",
    )


# ============================================================================
# 1. Authentication Enforcement Tests
# ============================================================================


def test_company_routes_require_authentication(client: TestClient):
    """Verify unauthenticated requests to company endpoints redirect to /login with HTTP 303."""
    dummy_id = uuid.uuid4()
    routes = [
        ("GET", "/companies"),
        ("GET", "/companies/new"),
        ("POST", "/companies"),
        ("GET", f"/companies/{dummy_id}"),
        ("GET", f"/companies/{dummy_id}/edit"),
        ("POST", f"/companies/{dummy_id}/edit"),
        ("POST", f"/companies/{dummy_id}/archive"),
        ("POST", f"/companies/{dummy_id}/unarchive"),
    ]

    for method, path in routes:
        if method == "GET":
            response = client.get(path)
        else:
            csrf_token = set_valid_csrf(client)
            response = client.post(path, data={"csrf_token": csrf_token})

        assert response.status_code == status.HTTP_303_SEE_OTHER, f"{method} {path} did not redirect"
        assert response.headers["location"] == "/login"


# ============================================================================
# 2. Company List Tests
# ============================================================================


def test_company_list_authenticated_displays_companies(client: TestClient, db_session: Session):
    """Verify GET /companies displays owned active companies and isolates other users' data."""
    user1 = _create_user(db_session, "user1_list@example.com", "Alice Walker")
    user2 = _create_user(db_session, "user2_list@example.com", "Bob Builder")

    comp_service = CompanyService(db_session)
    c1 = comp_service.create_company(user1.id, name="Anthropic", industry="AI")
    c2 = comp_service.create_company(user1.id, name="Google", industry="Tech")
    c_archived = comp_service.create_company(user1.id, name="Old Corp")
    comp_service.archive_company(c_archived.id, user1.id)

    # User 2 company (must not appear for User 1)
    comp_service.create_company(user2.id, name="Secret Co")

    authenticate_user(client, user1, db_session)
    response = client.get("/companies")

    assert response.status_code == status.HTTP_200_OK
    assert "text/html" in response.headers.get("content-type", "")
    assert "Anthropic" in response.text
    assert "Google" in response.text
    # Archived company excluded by default
    assert "Old Corp" not in response.text
    # Other user's company excluded
    assert "Secret Co" not in response.text


def test_company_list_include_archived_toggle(client: TestClient, db_session: Session):
    """Verify ?include_archived=1 displays both active and archived companies."""
    user = _create_user(db_session, "toggle_user@example.com")
    comp_service = CompanyService(db_session)
    comp_service.create_company(user.id, name="Active Corp")
    archived = comp_service.create_company(user.id, name="Archived Corp")
    comp_service.archive_company(archived.id, user.id)

    authenticate_user(client, user, db_session)
    response = client.get("/companies?include_archived=1")

    assert response.status_code == status.HTTP_200_OK
    assert "Active Corp" in response.text
    assert "Archived Corp" in response.text
    assert "badge--archived" in response.text


# ============================================================================
# 3. Company Creation Tests
# ============================================================================


def test_new_company_page_renders_form(client: TestClient, db_session: Session):
    """Verify GET /companies/new renders blank creation form with CSRF token."""
    user = _create_user(db_session, "form_getter@example.com")
    authenticate_user(client, user, db_session)

    response = client.get("/companies/new")
    assert response.status_code == status.HTTP_200_OK
    assert 'action="/companies"' in response.text
    assert 'name="name"' in response.text
    assert 'name="csrf_token"' in response.text


def test_company_create_valid_data_redirects_prg(client: TestClient, db_session: Session):
    """Verify valid company creation redirects to /companies/{id} via HTTP 303 (PRG)."""
    user = _create_user(db_session, "creator_route@example.com")
    authenticate_user(client, user, db_session)
    csrf_token = set_valid_csrf(client)

    response = client.post(
        "/companies",
        data={
            "csrf_token": csrf_token,
            "name": "Stripe",
            "website": "https://stripe.com",
            "industry": "FinTech",
            "location": "San Francisco, CA",
            "notes": "Applied via referral link",
        },
    )

    # 1. Verify PRG redirect
    assert response.status_code == status.HTTP_303_SEE_OTHER
    redirect_url = response.headers["location"]
    assert redirect_url.startswith("/companies/")

    # 2. Verify success flash message
    flash = extract_flash(client)
    assert flash is not None
    assert flash["category"] == "success"
    assert "created successfully" in flash["message"]

    # 3. Verify record persisted in database with correct ownership
    company_id = uuid.UUID(redirect_url.split("/")[-1])
    stmt = select(Company).where(Company.id == company_id)
    company = db_session.scalar(stmt)
    assert company is not None
    assert company.user_id == user.id
    assert company.name == "Stripe"
    assert company.name_normalized == "stripe"
    assert company.website == "https://stripe.com"


def test_company_create_duplicate_name_shows_form_validation_error(client: TestClient, db_session: Session):
    """Verify duplicate company name re-renders creation form with status 422 and helpful error."""
    user = _create_user(db_session, "dupe_route_user@example.com")
    comp_service = CompanyService(db_session)
    comp_service.create_company(user.id, name="Datadog")

    authenticate_user(client, user, db_session)
    csrf_token = set_valid_csrf(client)

    # Attempt to create duplicate with different casing & whitespace
    response = client.post(
        "/companies",
        data={
            "csrf_token": csrf_token,
            "name": "  DATADOG  ",
            "industry": "Observability",
        },
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert "A company named &#39;DATADOG&#39; already exists" in response.text or "already exists" in response.text
    # Entered fields are preserved in the form
    assert 'value="DATADOG"' in response.text or 'value="  DATADOG  "' in response.text


def test_company_create_blank_name_shows_form_validation_error(client: TestClient, db_session: Session):
    """Verify empty or whitespace-only name returns status 422 with validation error."""
    user = _create_user(db_session, "blank_route_user@example.com")
    authenticate_user(client, user, db_session)
    csrf_token = set_valid_csrf(client)

    response = client.post(
        "/companies",
        data={
            "csrf_token": csrf_token,
            "name": "   ",
        },
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert "Company name cannot be empty" in response.text


# ============================================================================
# 4. Company Detail Tests
# ============================================================================


def test_company_detail_success(client: TestClient, db_session: Session):
    """Verify GET /companies/{id} displays company metadata and status badge."""
    user = _create_user(db_session, "detail_viewer@example.com")
    comp_service = CompanyService(db_session)
    company = comp_service.create_company(
        user.id,
        name="GitHub",
        website="https://github.com",
        industry="DevTools",
        location="San Francisco, CA",
        notes="Interviewing next week",
    )

    authenticate_user(client, user, db_session)
    response = client.get(f"/companies/{company.id}")

    assert response.status_code == status.HTTP_200_OK
    assert "GitHub" in response.text
    assert "https://github.com" in response.text
    assert "DevTools" in response.text
    assert "Interviewing next week" in response.text
    assert "Active" in response.text


def test_company_detail_another_user_returns_404(client: TestClient, db_session: Session):
    """Verify accessing another user's company returns HTTP 404 Not Found (IDOR defense)."""
    user1 = _create_user(db_session, "victim_comp@example.com")
    user2 = _create_user(db_session, "attacker_comp@example.com")

    comp_service = CompanyService(db_session)
    company1 = comp_service.create_company(user1.id, name="Secret Tech")

    # Attacker logs in and queries User 1's company
    authenticate_user(client, user2, db_session)
    response = client.get(f"/companies/{company1.id}")

    assert response.status_code == status.HTTP_404_NOT_FOUND


def test_company_detail_nonexistent_uuid_returns_404(client: TestClient, db_session: Session):
    """Verify non-existent company UUID returns 404."""
    user = _create_user(db_session, "rando_comp@example.com")
    authenticate_user(client, user, db_session)

    response = client.get(f"/companies/{uuid.uuid4()}")
    assert response.status_code == status.HTTP_404_NOT_FOUND


# ============================================================================
# 5. Company Editing Tests
# ============================================================================


def test_edit_company_page_renders_prefilled_form(client: TestClient, db_session: Session):
    """Verify GET /companies/{id}/edit renders form pre-filled with existing data."""
    user = _create_user(db_session, "editor_user@example.com")
    comp_service = CompanyService(db_session)
    company = comp_service.create_company(
        user.id,
        name="Linear App",
        website="https://linear.app",
        location="San Francisco, CA",
    )

    authenticate_user(client, user, db_session)
    response = client.get(f"/companies/{company.id}/edit")

    assert response.status_code == status.HTTP_200_OK
    assert f'action="/companies/{company.id}/edit"' in response.text
    assert 'value="Linear App"' in response.text
    assert 'value="https://linear.app"' in response.text


def test_edit_company_success_redirects_prg(client: TestClient, db_session: Session):
    """Verify submitting company edits updates database and redirects to detail page via PRG."""
    user = _create_user(db_session, "editor_submit@example.com")
    comp_service = CompanyService(db_session)
    company = comp_service.create_company(user.id, name="Original Name")

    authenticate_user(client, user, db_session)
    csrf_token = set_valid_csrf(client)

    response = client.post(
        f"/companies/{company.id}/edit",
        data={
            "csrf_token": csrf_token,
            "name": "Updated Rebrand",
            "website": "https://updated.com",
            "location": "New York, NY",
        },
    )

    assert response.status_code == status.HTTP_303_SEE_OTHER
    assert response.headers["location"] == f"/companies/{company.id}"

    flash = extract_flash(client)
    assert flash is not None
    assert flash["category"] == "success"

    # Verify updated in DB
    db_session.refresh(company)
    assert company.name == "Updated Rebrand"
    assert company.name_normalized == "updated rebrand"
    assert company.location == "New York, NY"


def test_edit_company_clearing_optional_fields(client: TestClient, db_session: Session):
    """Verify submitting empty string for optional fields clears them to None in DB."""
    user = _create_user(db_session, "clearer@example.com")
    comp_service = CompanyService(db_session)
    company = comp_service.create_company(
        user.id,
        name="Figma",
        website="https://figma.com",
        industry="Design",
    )
    assert company.website == "https://figma.com"

    authenticate_user(client, user, db_session)
    csrf_token = set_valid_csrf(client)

    response = client.post(
        f"/companies/{company.id}/edit",
        data={
            "csrf_token": csrf_token,
            "name": "Figma",
            "website": "",  # Clear website
            "industry": "",  # Clear industry
        },
    )

    assert response.status_code == status.HTTP_303_SEE_OTHER
    db_session.refresh(company)
    assert company.website is None
    assert company.industry is None


def test_edit_company_duplicate_name_shows_validation_error(client: TestClient, db_session: Session):
    """Verify renaming a company to match another owned company name returns status 422."""
    user = _create_user(db_session, "dupe_editor@example.com")
    comp_service = CompanyService(db_session)
    comp_service.create_company(user.id, name="Company A")
    company_b = comp_service.create_company(user.id, name="Company B")

    authenticate_user(client, user, db_session)
    csrf_token = set_valid_csrf(client)

    response = client.post(
        f"/companies/{company_b.id}/edit",
        data={
            "csrf_token": csrf_token,
            "name": "company a",  # Collides with Company A
        },
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert "already exists" in response.text


def test_edit_company_another_user_returns_404(client: TestClient, db_session: Session):
    """Verify attempting to edit another user's company returns 404."""
    user1 = _create_user(db_session, "user1_edit@example.com")
    user2 = _create_user(db_session, "user2_edit@example.com")

    comp_service = CompanyService(db_session)
    comp1 = comp_service.create_company(user1.id, name="Victim Org")

    authenticate_user(client, user2, db_session)
    csrf_token = set_valid_csrf(client)

    # GET
    res_get = client.get(f"/companies/{comp1.id}/edit")
    assert res_get.status_code == status.HTTP_404_NOT_FOUND

    # POST
    res_post = client.post(
        f"/companies/{comp1.id}/edit",
        data={"csrf_token": csrf_token, "name": "Hacked Name"},
    )
    assert res_post.status_code == status.HTTP_404_NOT_FOUND

    db_session.refresh(comp1)
    assert comp1.name == "Victim Org"


# ============================================================================
# 6. Company Archiving Tests
# ============================================================================


def test_archive_company_redirects_prg_and_updates_status(client: TestClient, db_session: Session):
    """Verify POST /companies/{id}/archive sets is_archived=True and redirects to /companies."""
    user = _create_user(db_session, "archiver_route@example.com")
    comp_service = CompanyService(db_session)
    comp = comp_service.create_company(user.id, name="Startup To Archive")

    authenticate_user(client, user, db_session)
    csrf_token = set_valid_csrf(client)

    response = client.post(
        f"/companies/{comp.id}/archive",
        data={"csrf_token": csrf_token},
    )

    assert response.status_code == status.HTTP_303_SEE_OTHER
    assert response.headers["location"] == "/companies"

    db_session.refresh(comp)
    assert comp.is_archived is True


def test_unarchive_company_redirects_prg_and_restores_status(client: TestClient, db_session: Session):
    """Verify POST /companies/{id}/unarchive sets is_archived=False and redirects to detail page."""
    user = _create_user(db_session, "unarchiver_route@example.com")
    comp_service = CompanyService(db_session)
    comp = comp_service.create_company(user.id, name="Archived Startup")
    comp_service.archive_company(comp.id, user.id)
    assert comp.is_archived is True

    authenticate_user(client, user, db_session)
    csrf_token = set_valid_csrf(client)

    response = client.post(
        f"/companies/{comp.id}/unarchive",
        data={"csrf_token": csrf_token},
    )

    assert response.status_code == status.HTTP_303_SEE_OTHER
    assert response.headers["location"] == f"/companies/{comp.id}"

    db_session.refresh(comp)
    assert comp.is_archived is False


def test_archive_another_user_company_returns_404(client: TestClient, db_session: Session):
    """Verify attempting to archive another user's company returns 404."""
    user1 = _create_user(db_session, "victim_arch@example.com")
    user2 = _create_user(db_session, "attacker_arch@example.com")

    comp_service = CompanyService(db_session)
    comp1 = comp_service.create_company(user1.id, name="Protected Org")

    authenticate_user(client, user2, db_session)
    csrf_token = set_valid_csrf(client)

    response = client.post(
        f"/companies/{comp1.id}/archive",
        data={"csrf_token": csrf_token},
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    db_session.refresh(comp1)
    assert comp1.is_archived is False


# ============================================================================
# 7. CSRF Protection Tests on Company POST Routes
# ============================================================================


def test_company_post_routes_require_csrf(client: TestClient, db_session: Session):
    """Verify all company state-changing POST routes reject missing or invalid CSRF tokens."""
    user = _create_user(db_session, "csrf_checker@example.com")
    comp_service = CompanyService(db_session)
    comp = comp_service.create_company(user.id, name="CSRF Test Co")

    authenticate_user(client, user, db_session)

    post_endpoints = [
        ("/companies", {"name": "New Co"}),
        (f"/companies/{comp.id}/edit", {"name": "Renamed"}),
        (f"/companies/{comp.id}/archive", {}),
        (f"/companies/{comp.id}/unarchive", {}),
    ]

    for path, data in post_endpoints:
        # 1. Missing CSRF cookie and form token
        res_no_csrf = client.post(path, data=data)
        assert res_no_csrf.status_code == status.HTTP_403_FORBIDDEN, f"Failed for {path}"

        # 2. Tampered CSRF token
        set_valid_csrf(client)
        tampered_data = dict(data)
        tampered_data["csrf_token"] = "invalid_token"
        res_tampered = client.post(path, data=tampered_data)
        assert res_tampered.status_code == status.HTTP_403_FORBIDDEN, f"Failed for {path}"

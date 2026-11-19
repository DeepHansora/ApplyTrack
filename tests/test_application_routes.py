"""Integration tests for application routes, UI templates, ownership, and CSRF protection.

Verifies:
1. Authentication Enforcement:
   - All application routes require active session authentication.
   - Unauthenticated requests are redirected to /login with HTTP 303.
2. Application List (GET /applications):
   - Displays user's applications in table format.
   - Defaults to active applications.
   - Supports search, company filter, status filter, employment type, and date filters.
   - Supports sorting and pagination preserving query parameters.
   - Strict cross-user isolation: User B never sees User A's applications.
3. Application Creation (GET /applications/new & POST /applications):
   - Renders blank creation form with active company selector.
   - Warning banner when user has no active companies.
   - Initial status selection allowed on create (defaults to Saved).
   - Valid submission creates record and redirects to /applications/{id} (PRG) with flash.
   - Rejects unowned company, archived company, invalid salary range, or missing title with 422.
4. Application Detail (GET /applications/{id}):
   - Renders application metadata, company link, compensation, notes, and placeholders.
   - Accessing non-existent application returns 404.
   - Accessing another user's application returns 404 (IDOR defense).
5. Application Editing (GET & POST /applications/{id}/edit):
   - Renders pre-filled edit form.
   - Preserves archived company in selector if already associated.
   - Status and archive state cannot be changed via edit route.
   - Successful edit updates fields and redirects to /applications/{id} (PRG).
   - Accessing or editing another user's application returns 404.
6. Application Archiving & Unarchiving (POST /applications/{id}/archive & unarchive):
   - Archiving sets is_archived=True, preserves DB record, and redirects to /applications (PRG).
   - Unarchiving restores is_archived=False and redirects to /applications/{id} (PRG).
   - Archiving or unarchiving another user's application returns 404.
7. CSRF Protection:
   - All POST routes require valid CSRF cookies and matching form tokens.
   - Missing or invalid CSRF tokens return HTTP 403 Forbidden.
"""

from datetime import datetime, timezone
from decimal import Decimal
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
from app.database import engine
from app.dependencies import get_db
from app.main import app
from app.models.application import Application
from app.models.company import Company
from app.models.user import User
from app.schemas.application import ApplicationCreate
from app.schemas.company import CompanyCreate
from app.services.application_service import ApplicationService
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


def _create_company(db: Session, user: User, name: str = "Acme Corp") -> Company:
    """Helper to persist an active company."""
    service = CompanyService(db)
    c_out = service.create_company(user.id, CompanyCreate(name=name))
    return db.get(Company, c_out.id)


def _create_application(
    db: Session,
    user: User,
    company: Company,
    job_title: str = "Software Engineer",
    status_val: str = "Saved",
    **kwargs,
) -> Application:
    """Helper to persist an application."""
    service = ApplicationService(db)
    app_in = ApplicationCreate(
        company_id=company.id,
        job_title=job_title,
        status=status_val,
        **kwargs,
    )
    app_out = service.create_application(user.id, app_in)
    return db.get(Application, app_out.id)


# ============================================================================
# 1. Authentication Enforcement Tests
# ============================================================================


def test_application_routes_require_authentication(client: TestClient):
    """Verify unauthenticated requests to application endpoints redirect to /login with HTTP 303."""
    dummy_id = uuid.uuid4()
    routes = [
        ("GET", "/applications"),
        ("GET", "/applications/new"),
        ("POST", "/applications"),
        ("GET", f"/applications/{dummy_id}"),
        ("GET", f"/applications/{dummy_id}/edit"),
        ("POST", f"/applications/{dummy_id}/edit"),
        ("POST", f"/applications/{dummy_id}/archive"),
        ("POST", f"/applications/{dummy_id}/unarchive"),
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
# 2. Application List Tests
# ============================================================================


def test_applications_list_renders_empty_state_for_new_user(client: TestClient, db_session: Session):
    """Verify GET /applications shows empty state when user has no applications."""
    user = _create_user(db_session, "app_list1@example.com")
    authenticate_user(client, user, db_session)

    response = client.get("/applications")
    assert response.status_code == status.HTTP_200_OK
    assert "Applications" in response.text
    assert "No applications recorded yet" in response.text
    assert "+ Add First Application" in response.text


def test_applications_list_displays_user_applications_and_badges(client: TestClient, db_session: Session):
    """Verify GET /applications displays job titles, company names, and status badges."""
    user = _create_user(db_session, "app_list2@example.com")
    company = _create_company(db_session, user, name="Stripe")
    _create_application(db_session, user, company, job_title="Backend Engineer", status_val="Applied")
    authenticate_user(client, user, db_session)

    response = client.get("/applications")
    assert response.status_code == status.HTTP_200_OK
    assert "Backend Engineer" in response.text
    assert "Stripe" in response.text
    assert "badge--status-applied" in response.text
    assert "Applied" in response.text


def test_applications_list_user_isolation(client: TestClient, db_session: Session):
    """Verify User B cannot see User A's applications on the listing page."""
    user_a = _create_user(db_session, "user_a@example.com")
    company_a = _create_company(db_session, user_a, name="Company Alpha")
    _create_application(db_session, user_a, company_a, job_title="Secret Project Lead")

    user_b = _create_user(db_session, "user_b@example.com")
    authenticate_user(client, user_b, db_session)

    response = client.get("/applications")
    assert response.status_code == status.HTTP_200_OK
    assert "Secret Project Lead" not in response.text
    assert "Company Alpha" not in response.text


def test_applications_list_filters_by_search_and_status(client: TestClient, db_session: Session):
    """Verify searching and status filtering on GET /applications."""
    user = _create_user(db_session, "app_filter@example.com")
    comp_google = _create_company(db_session, user, name="Google")
    comp_meta = _create_company(db_session, user, name="Meta")

    _create_application(db_session, user, comp_google, job_title="Staff ML Engineer", status_val="Interview")
    _create_application(db_session, user, comp_meta, job_title="Product Manager", status_val="Saved")

    authenticate_user(client, user, db_session)

    # Search for "ML Engineer"
    resp1 = client.get("/applications?search=ML")
    assert resp1.status_code == status.HTTP_200_OK
    assert "Staff ML Engineer" in resp1.text
    assert "Product Manager" not in resp1.text

    # Filter by status "Saved"
    resp2 = client.get("/applications?status=Saved")
    assert resp2.status_code == status.HTTP_200_OK
    assert "Product Manager" in resp2.text
    assert "Staff ML Engineer" not in resp2.text


def test_applications_list_company_id_query_parameter_validation(client: TestClient, db_session: Session):
    """Verify company_id query parameter handling:
    - company_id= (empty) returns HTTP 200 (all applications).
    - company_id= (whitespace) returns HTTP 200 (all applications).
    - Missing company_id returns HTTP 200 (all applications).
    - Valid company UUID filters correctly.
    - Invalid non-empty company UUID is rejected with HTTP 422.
    """
    user = _create_user(db_session, "app_comp_filter@example.com")
    comp_a = _create_company(db_session, user, name="Company Alpha")
    comp_b = _create_company(db_session, user, name="Company Beta")

    _create_application(db_session, user, comp_a, job_title="Alpha Engineer")
    _create_application(db_session, user, comp_b, job_title="Beta Engineer")

    authenticate_user(client, user, db_session)

    # 1. Missing company_id returns HTTP 200 and displays all
    resp_missing = client.get("/applications")
    assert resp_missing.status_code == status.HTTP_200_OK
    assert "Alpha Engineer" in resp_missing.text
    assert "Beta Engineer" in resp_missing.text

    # 2. company_id= (empty string) returns HTTP 200 and displays all
    resp_empty = client.get("/applications?company_id=")
    assert resp_empty.status_code == status.HTTP_200_OK
    assert "Alpha Engineer" in resp_empty.text
    assert "Beta Engineer" in resp_empty.text

    # 3. company_id=   (whitespace only) returns HTTP 200 and displays all
    resp_whitespace = client.get("/applications?company_id=%20%20%20")
    assert resp_whitespace.status_code == status.HTTP_200_OK
    assert "Alpha Engineer" in resp_whitespace.text
    assert "Beta Engineer" in resp_whitespace.text

    # 4. Valid company UUID filters correctly
    resp_valid = client.get(f"/applications?company_id={comp_a.id}")
    assert resp_valid.status_code == status.HTTP_200_OK
    assert "Alpha Engineer" in resp_valid.text
    assert "Beta Engineer" not in resp_valid.text

    # 5. Invalid non-empty company UUID is rejected with HTTP 422
    resp_invalid = client.get("/applications?company_id=not-a-valid-uuid")
    assert resp_invalid.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_applications_list_archive_filter(client: TestClient, db_session: Session):
    """Verify archive_status filters active vs archived records and renders unarchive buttons."""
    user = _create_user(db_session, "app_archive_filter@example.com")
    comp = _create_company(db_session, user, name="GitHub")
    app1 = _create_application(db_session, user, comp, job_title="Active Job")
    app2 = _create_application(db_session, user, comp, job_title="Archived Job")

    app_service = ApplicationService(db_session)
    app_service.archive_application(app2.id, user.id)

    authenticate_user(client, user, db_session)

    # Default: active only (Active Records is default in dropdown)
    resp_default = client.get("/applications")
    assert resp_default.status_code == status.HTTP_200_OK
    assert "Active Job" in resp_default.text
    assert "Archived Job" not in resp_default.text
    assert 'value="active" selected' in resp_default.text

    # Explicit active
    resp_active = client.get("/applications?archive_status=active")
    assert resp_active.status_code == status.HTTP_200_OK
    assert "Active Job" in resp_active.text
    assert "Archived Job" not in resp_active.text

    # Archived only: displays archived applications and includes Unarchive action button
    resp_archived = client.get("/applications?archive_status=archived")
    assert resp_archived.status_code == status.HTTP_200_OK
    assert "Active Job" not in resp_archived.text
    assert "Archived Job" in resp_archived.text
    assert f'action="/applications/{app2.id}/unarchive"' in resp_archived.text
    assert "Unarchive" in resp_archived.text
    assert 'value="archived" selected' in resp_archived.text

    # All records: displays both
    resp_all = client.get("/applications?archive_status=all")
    assert resp_all.status_code == status.HTTP_200_OK
    assert "Active Job" in resp_all.text
    assert "Archived Job" in resp_all.text
    assert 'value="all" selected' in resp_all.text


def test_applications_list_archive_filter_persists_during_pagination(client: TestClient, db_session: Session):
    """Verify archive_status parameter is preserved when navigating pagination pages."""
    user = _create_user(db_session, "app_arch_page@example.com")
    comp = _create_company(db_session, user, name="GitLab")
    app_service = ApplicationService(db_session)

    for i in range(12):
        app = _create_application(db_session, user, comp, job_title=f"Archived Role {i:02d}")
        app_service.archive_application(app.id, user.id)

    authenticate_user(client, user, db_session)

    # Request page 1 of archived records with 5 per page
    resp = client.get("/applications?archive_status=archived&per_page=5&page=1")
    assert resp.status_code == status.HTTP_200_OK
    assert "Page 1 of 3" in resp.text
    assert "archive_status=archived" in resp.text
    assert "per_page=5" in resp.text
    assert "page=2" in resp.text


def test_applications_list_unarchive_flow_from_table(client: TestClient, db_session: Session):
    """Verify unarchiving an application from the table restores it to active listings."""
    user = _create_user(db_session, "app_table_unarch@example.com")
    comp = _create_company(db_session, user, name="DigitalOcean")
    app = _create_application(db_session, user, comp, job_title="Cloud Engineer")

    # Initially archive the application
    app_service = ApplicationService(db_session)
    app_service.archive_application(app.id, user.id)

    authenticate_user(client, user, db_session)

    # 1. Verify Unarchive action is rendered in the archived list
    resp_arch = client.get("/applications?archive_status=archived")
    assert resp_arch.status_code == status.HTTP_200_OK
    assert "Cloud Engineer" in resp_arch.text
    assert f'action="/applications/{app.id}/unarchive"' in resp_arch.text

    # 2. Unarchiving without CSRF token fails with 403
    resp_no_csrf = client.post(f"/applications/{app.id}/unarchive")
    assert resp_no_csrf.status_code == status.HTTP_403_FORBIDDEN

    # 3. Unarchiving another user's application fails with 404
    other_user = _create_user(db_session, "other_user_unarch@example.com")
    other_comp = _create_company(db_session, other_user, name="Other Inc")
    other_app = _create_application(db_session, other_user, other_comp, job_title="Other Role")
    app_service.archive_application(other_app.id, other_user.id)

    csrf_token = set_valid_csrf(client)
    resp_other = client.post(
        f"/applications/{other_app.id}/unarchive",
        data={"csrf_token": csrf_token},
    )
    assert resp_other.status_code == status.HTTP_404_NOT_FOUND

    # 4. Perform valid unarchive
    csrf_token = set_valid_csrf(client)
    resp_unarch = client.post(
        f"/applications/{app.id}/unarchive",
        data={"csrf_token": csrf_token},
    )
    assert resp_unarch.status_code == status.HTTP_303_SEE_OTHER
    assert resp_unarch.headers["location"] == f"/applications/{app.id}"

    flash = extract_flash(client)
    assert flash is not None
    assert flash["category"] == "success"
    assert "unarchived" in flash["message"]

    # 5. Restored application is now immediately visible under Active Records
    resp_active = client.get("/applications")
    assert resp_active.status_code == status.HTTP_200_OK
    assert "Cloud Engineer" in resp_active.text

    # And no longer present under Archived Records
    resp_arch_after = client.get("/applications?archive_status=archived")
    assert resp_arch_after.status_code == status.HTTP_200_OK
    assert "Cloud Engineer" not in resp_arch_after.text


def test_applications_list_pagination_and_query_preservation(client: TestClient, db_session: Session):
    """Verify pagination supports 5, 10, and 20 per page, defaults to 20, and preserves parameters."""
    user = _create_user(db_session, "app_page@example.com")
    comp = _create_company(db_session, user, name="Amazon")

    # Create 15 applications
    for i in range(15):
        _create_application(db_session, user, comp, job_title=f"Engineer {i:02d}")

    authenticate_user(client, user, db_session)

    # 1. 5 per page -> 3 total pages (ceil(15/5) = 3)
    resp_5 = client.get("/applications?per_page=5&page=1&search=Engineer")
    assert resp_5.status_code == status.HTTP_200_OK
    assert "Page 1 of 3" in resp_5.text
    assert 'value="5" selected' in resp_5.text
    assert "per_page=5" in resp_5.text
    assert "page=2" in resp_5.text

    # 2. 10 per page -> 2 total pages (ceil(15/10) = 2)
    resp_10 = client.get("/applications?per_page=10&page=1&search=Engineer")
    assert resp_10.status_code == status.HTTP_200_OK
    assert "Page 1 of 2" in resp_10.text
    assert 'value="10" selected' in resp_10.text
    assert "per_page=10" in resp_10.text
    assert "page=2" in resp_10.text

    # 3. 20 per page -> 1 total page (all 15 items fit, no pagination controls rendered)
    resp_20 = client.get("/applications?per_page=20&page=1&search=Engineer")
    assert resp_20.status_code == status.HTTP_200_OK
    assert 'value="20" selected' in resp_20.text
    assert "Showing 1–15 of 15 applications" in resp_20.text

    # 4. Invalid page size (e.g. 50 or -5) falls back to 20
    resp_invalid_size = client.get("/applications?per_page=50&page=1")
    assert resp_invalid_size.status_code == status.HTTP_200_OK
    assert "Showing 1–15 of 15 applications" in resp_invalid_size.text

    # 5. Changing page size via filter form (without page parameter) resets to page 1
    resp_reset = client.get("/applications?per_page=5")
    assert resp_reset.status_code == status.HTTP_200_OK
    assert "Page 1 of 3" in resp_reset.text


# ============================================================================
# 3. Application Creation Tests
# ============================================================================


def test_application_creation_page_shows_warning_without_companies(client: TestClient, db_session: Session):
    """Verify GET /applications/new shows warning if user has no active companies."""
    user = _create_user(db_session, "app_no_comp@example.com")
    authenticate_user(client, user, db_session)

    response = client.get("/applications/new")
    assert response.status_code == status.HTTP_200_OK
    assert "Add an Active Company First" in response.text
    assert "/companies/new" in response.text


def test_application_creation_page_renders_companies_and_statuses(client: TestClient, db_session: Session):
    """Verify GET /applications/new lists active companies and statuses."""
    user = _create_user(db_session, "app_comp_list@example.com")
    _create_company(db_session, user, name="Apple")
    authenticate_user(client, user, db_session)

    response = client.get("/applications/new")
    assert response.status_code == status.HTTP_200_OK
    assert "Apple" in response.text
    assert "Saved" in response.text
    assert "Initial Status" in response.text


def test_application_creation_success_prg(client: TestClient, db_session: Session):
    """Verify POST /applications creates application, redirects via PRG, and flashes success."""
    user = _create_user(db_session, "app_create_ok@example.com")
    company = _create_company(db_session, user, name="Netflix")
    authenticate_user(client, user, db_session)
    csrf_token = set_valid_csrf(client)

    payload = {
        "csrf_token": csrf_token,
        "company_id": str(company.id),
        "job_title": "Senior Distributed Systems Engineer",
        "status": "Applied",
        "employment_type": "Full-time",
        "location": "Los Gatos, CA",
        "salary_min": "180000",
        "salary_max": "240000",
        "currency": "USD",
        "applied_at": "2026-05-15",
        "job_description": "Architect high-throughput streaming systems.",
        "notes": "Referred by Alice.",
    }

    response = client.post("/applications", data=payload)
    assert response.status_code == status.HTTP_303_SEE_OTHER

    new_app = db_session.execute(
        select(Application).where(Application.user_id == user.id, Application.job_title == payload["job_title"])
    ).scalar_one()

    assert response.headers["location"] == f"/applications/{new_app.id}"
    assert new_app.company_id == company.id
    assert new_app.status == "Applied"
    assert new_app.salary_min == Decimal("180000")
    assert new_app.salary_max == Decimal("240000")
    assert new_app.currency == "USD"
    assert new_app.applied_at == datetime(2026, 5, 15, tzinfo=timezone.utc)
    assert new_app.notes == "Referred by Alice."

    flash = extract_flash(client)
    assert flash is not None
    assert flash["category"] == "success"
    assert "Senior Distributed Systems Engineer" in flash["message"]


def test_application_creation_rejects_unowned_company(client: TestClient, db_session: Session):
    """Verify POST /applications rejects company belonging to another user with 422."""
    user_a = _create_user(db_session, "comp_owner_a@example.com")
    company_a = _create_company(db_session, user_a, name="Company A")

    user_b = _create_user(db_session, "comp_owner_b@example.com")
    authenticate_user(client, user_b, db_session)
    csrf_token = set_valid_csrf(client)

    payload = {
        "csrf_token": csrf_token,
        "company_id": str(company_a.id),
        "job_title": "Unauthorized Role",
    }

    response = client.post("/applications", data=payload)
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert "Selected company not found or does not belong to the current user" in response.text


def test_application_creation_rejects_archived_company(client: TestClient, db_session: Session):
    """Verify POST /applications rejects linking to an archived company with 422."""
    user = _create_user(db_session, "archived_comp_app@example.com")
    company = _create_company(db_session, user, name="Old Corp")
    CompanyService(db_session).archive_company(company.id, user.id)

    authenticate_user(client, user, db_session)
    csrf_token = set_valid_csrf(client)

    payload = {
        "csrf_token": csrf_token,
        "company_id": str(company.id),
        "job_title": "Invalid Role",
    }

    response = client.post("/applications", data=payload)
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert "Cannot associate a new application with an archived company" in response.text


def test_application_creation_rejects_invalid_salary_range(client: TestClient, db_session: Session):
    """Verify POST /applications rejects salary_min > salary_max with 422."""
    user = _create_user(db_session, "bad_salary@example.com")
    company = _create_company(db_session, user, name="Tech Co")
    authenticate_user(client, user, db_session)
    csrf_token = set_valid_csrf(client)

    payload = {
        "csrf_token": csrf_token,
        "company_id": str(company.id),
        "job_title": "Dev",
        "salary_min": "200000",
        "salary_max": "100000",
    }

    response = client.post("/applications", data=payload)
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert "Minimum salary cannot exceed maximum salary" in response.text


def test_application_creation_rejects_blank_job_title(client: TestClient, db_session: Session):
    """Verify POST /applications rejects blank job title with 422."""
    user = _create_user(db_session, "blank_title@example.com")
    company = _create_company(db_session, user, name="Acme")
    authenticate_user(client, user, db_session)
    csrf_token = set_valid_csrf(client)

    payload = {
        "csrf_token": csrf_token,
        "company_id": str(company.id),
        "job_title": "   ",
    }

    response = client.post("/applications", data=payload)
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert "Job title cannot be empty" in response.text


# ============================================================================
# 4. Application Detail Tests
# ============================================================================


def test_application_detail_renders_data(client: TestClient, db_session: Session):
    """Verify GET /applications/{id} renders application metadata and placeholders."""
    user = _create_user(db_session, "app_detail@example.com")
    company = _create_company(db_session, user, name="Datadog")
    app = _create_application(
        db_session,
        user,
        company,
        job_title="Core Platform Engineer",
        status_val="Interview",
        employment_type="Full-time",
        location="Remote, US",
        salary_min=Decimal("170000"),
        salary_max=Decimal("200000"),
        currency="USD",
        notes="Interview with VP of Eng scheduled.",
    )

    authenticate_user(client, user, db_session)

    response = client.get(f"/applications/{app.id}")
    assert response.status_code == status.HTTP_200_OK
    assert "Core Platform Engineer" in response.text
    assert "Datadog" in response.text
    assert "Interview" in response.text
    assert "170,000 – 200,000 USD" in response.text
    assert "Interview with VP of Eng scheduled." in response.text
    assert "Phase 4: Upcoming" in response.text
    assert "Phase 5: Upcoming" in response.text


def test_application_detail_idor_defense(client: TestClient, db_session: Session):
    """Verify User B cannot view User A's application by ID (returns 404)."""
    user_a = _create_user(db_session, "app_user_a@example.com")
    comp_a = _create_company(db_session, user_a, name="Company A")
    app_a = _create_application(db_session, user_a, comp_a, job_title="Classified Role")

    user_b = _create_user(db_session, "app_user_b@example.com")
    authenticate_user(client, user_b, db_session)

    response = client.get(f"/applications/{app_a.id}")
    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert "Application not found" in response.text


def test_application_detail_nonexistent_returns_404(client: TestClient, db_session: Session):
    """Verify GET /applications/{nonexistent_id} returns 404."""
    user = _create_user(db_session, "app_not_found@example.com")
    authenticate_user(client, user, db_session)

    response = client.get(f"/applications/{uuid.uuid4()}")
    assert response.status_code == status.HTTP_404_NOT_FOUND


# ============================================================================
# 5. Application Edit Tests
# ============================================================================


def test_application_edit_page_renders_prefilled_form(client: TestClient, db_session: Session):
    """Verify GET /applications/{id}/edit renders existing data and prevents status modification."""
    user = _create_user(db_session, "app_edit_prefill@example.com")
    company = _create_company(db_session, user, name="Snowflake")
    app = _create_application(
        db_session,
        user,
        company,
        job_title="Data Platform Architect",
        location="Bellevue, WA",
    )

    authenticate_user(client, user, db_session)

    response = client.get(f"/applications/{app.id}/edit")
    assert response.status_code == status.HTTP_200_OK
    assert "Edit Application" in response.text
    assert "Data Platform Architect" in response.text
    assert "Bellevue, WA" in response.text
    # Status is read-only badge on edit form
    assert "Status lifecycle transitions are managed in Phase 4" in response.text


def test_application_edit_preserves_archived_company_in_selector(client: TestClient, db_session: Session):
    """Verify GET /applications/{id}/edit preserves associated archived company in dropdown."""
    user = _create_user(db_session, "app_edit_archived_comp@example.com")
    company = _create_company(db_session, user, name="Archived Employer")
    app = _create_application(db_session, user, company, job_title="Legacy Architect")

    # Now archive the company
    CompanyService(db_session).archive_company(company.id, user.id)

    authenticate_user(client, user, db_session)

    response = client.get(f"/applications/{app.id}/edit")
    assert response.status_code == status.HTTP_200_OK
    assert "Archived Employer (Archived)" in response.text


def test_application_edit_success_prg(client: TestClient, db_session: Session):
    """Verify POST /applications/{id}/edit updates fields and redirects to detail page via PRG."""
    user = _create_user(db_session, "app_edit_ok@example.com")
    comp1 = _create_company(db_session, user, name="Initial Co")
    comp2 = _create_company(db_session, user, name="New Co")
    app = _create_application(db_session, user, comp1, job_title="Junior Engineer")

    authenticate_user(client, user, db_session)
    csrf_token = set_valid_csrf(client)

    payload = {
        "csrf_token": csrf_token,
        "company_id": str(comp2.id),
        "job_title": "Senior Engineer",
        "employment_type": "Contract",
        "location": "New York, NY",
        "salary_min": "130000",
        "salary_max": "160000",
        "currency": "EUR",
        "applied_at": "2026-06-01",
        "job_description": "Updated job description.",
        "notes": "Updated candidate notes.",
    }

    response = client.post(f"/applications/{app.id}/edit", data=payload)
    assert response.status_code == status.HTTP_303_SEE_OTHER
    assert response.headers["location"] == f"/applications/{app.id}"

    db_session.refresh(app)
    assert app.job_title == "Senior Engineer"
    assert app.company_id == comp2.id
    assert app.employment_type == "Contract"
    assert app.currency == "EUR"
    assert app.applied_at == datetime(2026, 6, 1, tzinfo=timezone.utc)

    flash = extract_flash(client)
    assert flash is not None
    assert flash["category"] == "success"
    assert "Senior Engineer" in flash["message"]


def test_application_edit_idor_defense(client: TestClient, db_session: Session):
    """Verify User B cannot edit User A's application (returns 404)."""
    user_a = _create_user(db_session, "app_edit_a@example.com")
    comp_a = _create_company(db_session, user_a, name="Company Alpha")
    app_a = _create_application(db_session, user_a, comp_a, job_title="Lead")

    user_b = _create_user(db_session, "app_edit_b@example.com")
    authenticate_user(client, user_b, db_session)
    csrf_token = set_valid_csrf(client)

    # GET edit
    resp_get = client.get(f"/applications/{app_a.id}/edit")
    assert resp_get.status_code == status.HTTP_404_NOT_FOUND

    # POST edit
    resp_post = client.post(
        f"/applications/{app_a.id}/edit",
        data={"csrf_token": csrf_token, "job_title": "Hijacked Title"},
    )
    assert resp_post.status_code == status.HTTP_404_NOT_FOUND


# ============================================================================
# 6. Application Archiving & Unarchiving Tests
# ============================================================================


def test_application_archive_and_unarchive_flow(client: TestClient, db_session: Session):
    """Verify archiving application soft-deletes it, and unarchiving restores it."""
    user = _create_user(db_session, "app_arch_unarch@example.com")
    comp = _create_company(db_session, user, name="Palantir")
    app = _create_application(db_session, user, comp, job_title="Forward Deployed Engineer")

    authenticate_user(client, user, db_session)

    # Archive
    csrf_token = set_valid_csrf(client)
    resp_archive = client.post(f"/applications/{app.id}/archive", data={"csrf_token": csrf_token})
    assert resp_archive.status_code == status.HTTP_303_SEE_OTHER
    assert resp_archive.headers["location"] == "/applications"

    db_session.refresh(app)
    assert app.is_archived is True

    flash_arch = extract_flash(client)
    assert flash_arch is not None
    assert flash_arch["category"] == "info"
    assert "archived" in flash_arch["message"]

    # Unarchive
    csrf_token2 = set_valid_csrf(client)
    resp_unarchive = client.post(f"/applications/{app.id}/unarchive", data={"csrf_token": csrf_token2})
    assert resp_unarchive.status_code == status.HTTP_303_SEE_OTHER
    assert resp_unarchive.headers["location"] == f"/applications/{app.id}"

    db_session.refresh(app)
    assert app.is_archived is False

    flash_unarch = extract_flash(client)
    assert flash_unarch is not None
    assert flash_unarch["category"] == "success"
    assert "unarchived" in flash_unarch["message"]


def test_application_archive_idor_defense(client: TestClient, db_session: Session):
    """Verify User B cannot archive or unarchive User A's application (returns 404)."""
    user_a = _create_user(db_session, "app_arch_a@example.com")
    comp_a = _create_company(db_session, user_a, name="Company A")
    app_a = _create_application(db_session, user_a, comp_a, job_title="Target")

    user_b = _create_user(db_session, "app_arch_b@example.com")
    authenticate_user(client, user_b, db_session)

    csrf1 = set_valid_csrf(client)
    resp_arch = client.post(f"/applications/{app_a.id}/archive", data={"csrf_token": csrf1})
    assert resp_arch.status_code == status.HTTP_404_NOT_FOUND

    csrf2 = set_valid_csrf(client)
    resp_unarch = client.post(f"/applications/{app_a.id}/unarchive", data={"csrf_token": csrf2})
    assert resp_unarch.status_code == status.HTTP_404_NOT_FOUND


# ============================================================================
# 7. CSRF Protection Tests
# ============================================================================


def test_application_post_routes_reject_missing_or_invalid_csrf(client: TestClient, db_session: Session):
    """Verify CSRF token validation protects all application mutation endpoints."""
    user = _create_user(db_session, "app_csrf@example.com")
    comp = _create_company(db_session, user, name="Secure Corp")
    app = _create_application(db_session, user, comp, job_title="Security Lead")

    authenticate_user(client, user, db_session)

    post_endpoints = [
        ("/applications", {"company_id": str(comp.id), "job_title": "Role"}),
        (f"/applications/{app.id}/edit", {"job_title": "Role 2"}),
        (f"/applications/{app.id}/archive", {}),
        (f"/applications/{app.id}/unarchive", {}),
    ]

    for path, data in post_endpoints:
        # 1. Missing CSRF entirely
        resp_missing = client.post(path, data=data)
        assert resp_missing.status_code == status.HTTP_403_FORBIDDEN, f"{path} accepted missing CSRF"

        # 2. Invalid/tampered CSRF token
        set_valid_csrf(client)
        bad_data = {**data, "csrf_token": "tampered_invalid_token"}
        resp_invalid = client.post(path, data=bad_data)
        assert resp_invalid.status_code == status.HTTP_403_FORBIDDEN, f"{path} accepted invalid CSRF"

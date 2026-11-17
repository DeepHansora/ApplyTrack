"""Tests for public router endpoints, landing page rendering, and health checks."""

from unittest.mock import MagicMock
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.main import app
from app.dependencies import get_db

client = TestClient(app)


def test_landing_page_renders_html_with_200():
    """Verify that GET / returns HTTP 200, Content-Type text/html, and semantic structure."""
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    html = response.text

    # Verify document and page title
    assert "<!DOCTYPE html>" in html
    assert "<title>ApplyTrack — Your Search, Structured</title>" in html

    # Verify hero section
    assert "Your search," in html
    assert "structured." in html
    assert "A job application management platform built for clarity, control, and career momentum." in html
    assert "Get Started" in html
    assert "Explore Features" in html

    # Verify core feature sections
    assert "Application Lifecycle Tracking" in html
    assert "Interview Round Management" in html
    assert "Resume Reference Registry" in html
    assert "In-App Reminders &amp; Deadlines" in html or "In-App Reminders & Deadlines" in html
    assert "AI-Assisted Resume Matching" in html
    assert "Google Gemini" in html

    # Verify footer branding
    assert "Built with intent." in html


def test_health_check_healthy_database_returns_200():
    """Verify that GET /health continues to return HTTP 200 when database is reachable."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "healthy",
        "database": "connected",
    }


def test_health_check_database_failure_returns_503():
    """Verify that GET /health returns HTTP 503 without leaking credentials when database fails."""
    mock_session = MagicMock(spec=Session)
    mock_session.execute.side_effect = OperationalError(
        statement="SELECT 1",
        params={},
        orig=Exception("Simulated connection failure"),
    )

    def mock_get_db_failure():
        yield mock_session

    app.dependency_overrides[get_db] = mock_get_db_failure

    try:
        response = client.get("/health")
        assert response.status_code == 503
        data = response.json()
        assert data["detail"] == "Service unavailable: database connection check failed"
        raw_text = response.text.lower()
        assert "password" not in raw_text
        assert "postgres" not in raw_text
        assert "localhost" not in raw_text
    finally:
        app.dependency_overrides.clear()

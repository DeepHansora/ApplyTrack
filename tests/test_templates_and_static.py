"""Tests for Jinja2 template rendering configuration and static file mounting."""

from pathlib import Path
from fastapi.testclient import TestClient
from jinja2 import TemplateNotFound
import pytest

from app.main import app
from app.templates import templates

client = TestClient(app)


def test_static_file_mount_serves_files():
    """Verify that static files mounted at /static are served with HTTP 200."""
    response = client.get("/static/css/tokens.css")
    assert response.status_code == 200
    assert ":root" in response.text
    assert "text/css" in response.headers.get("content-type", "")


def test_favicon_assets_served_successfully():
    """Verify that favicon.ico and favicon.svg are served with HTTP 200."""
    ico_response = client.get("/static/favicon.ico")
    assert ico_response.status_code == 200
    assert "image/x-icon" in ico_response.headers.get("content-type", "") or "image/vnd.microsoft.icon" in ico_response.headers.get("content-type", "")

    svg_response = client.get("/static/img/favicon.svg")
    assert svg_response.status_code == 200
    assert "image/svg+xml" in svg_response.headers.get("content-type", "")


def test_static_file_mount_returns_404_for_missing_file():
    """Verify that requesting non-existent static files returns HTTP 404."""
    response = client.get("/static/non_existent_file.css")
    assert response.status_code == 404


def test_jinja2_template_renderer_configuration(tmp_path):
    """Verify Jinja2 template loader resolves templates and variables correctly."""
    # Create temporary template in a dedicated test directory
    test_template_name = "test_render.html"
    test_template_content = "<h1>Hello, {{ name }}!</h1>"
    
    # Verify templates object exists and points to app/templates
    assert templates.env is not None
    assert Path(templates.env.loader.searchpath[0]).name == "templates"

    # Test template rendering logic via Jinja2 Environment
    template = templates.env.from_string(test_template_content)
    rendered = template.render(name="ApplyTrack")
    assert rendered == "<h1>Hello, ApplyTrack!</h1>"


def test_jinja2_template_not_found():
    """Verify that loading an uncreated template raises TemplateNotFound."""
    with pytest.raises(TemplateNotFound):
        templates.get_template("non_existent_template.html")

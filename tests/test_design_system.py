"""Tests for ApplyTrack design system tokens, CSS serving, and base template layout."""

from fastapi.testclient import TestClient
from jinja2 import Environment, FileSystemLoader

from app.main import app
from app.templates import TEMPLATES_DIR

client = TestClient(app)


def test_tokens_css_served_successfully():
    """Verify tokens.css is served with HTTP 200 and contains defined design tokens."""
    response = client.get("/static/css/tokens.css")
    assert response.status_code == 200
    assert "text/css" in response.headers.get("content-type", "")
    text = response.text
    # Verify primary design tokens from specification
    assert "--color-bg-primary: #F2F2F2;" in text
    assert "--color-text-primary: #111111;" in text
    assert "--color-muted-gray: #B6B5B5;" in text
    assert "--color-text-secondary: #838282;" in text
    assert "--color-dark-surface: #1E1E1E;" in text
    assert "--font-family-heading: 'Clash Display'" in text
    assert "--font-family-body: 'Satoshi'" in text


def test_style_css_served_successfully():
    """Verify style.css is served with HTTP 200 and imports tokens.css."""
    response = client.get("/static/css/style.css")
    assert response.status_code == 200
    assert "text/css" in response.headers.get("content-type", "")
    assert "@import url('tokens.css');" in response.text
    assert ".site-container" in response.text
    assert ".site-header" in response.text
    assert ".site-footer" in response.text


def test_base_template_inheritance_and_rendering():
    """Verify base.html renders semantic HTML5 layout and supports block overrides."""
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
    
    # Mock url_for helper for static paths inside template rendering
    def mock_url_for(endpoint, path=""):
        return f"/static/{path}"
    
    env.globals["url_for"] = mock_url_for

    # Child template extending base.html
    child_template_content = """
    {% extends "base.html" %}
    {% block title %}Custom Child Page{% endblock %}
    {% block content %}
    <section class="test-section">
      <h2>Child Content Header</h2>
      <p>Dynamic Content Paragraph</p>
    </section>
    {% endblock %}
    """
    template = env.from_string(child_template_content)
    rendered = template.render()

    # Verify semantic HTML5 elements
    assert "<!DOCTYPE html>" in rendered
    assert '<header class="site-header"' in rendered
    assert '<main class="main-content"' in rendered
    assert '<footer class="site-footer"' in rendered

    # Verify partials included properly
    assert "ApplyTrack" in rendered
    assert "Get Started" in rendered

    # Verify child block override
    assert "<title>Custom Child Page</title>" in rendered
    assert "Child Content Header" in rendered
    assert "Dynamic Content Paragraph" in rendered


def test_main_js_served_successfully():
    """Verify that main.js is served with HTTP 200 and implements accessible mobile navigation."""
    response = client.get("/static/js/main.js")
    assert response.status_code == 200
    assert "javascript" in response.headers.get("content-type", "")
    content = response.text
    # Verify core accessible interaction logic
    assert "DOMContentLoaded" in content
    assert ".menu-toggle" in content
    assert "#primary-navigation" in content
    assert "aria-expanded" in content
    assert "site-nav--open" in content
    assert "Escape" in content


def test_mobile_header_accessibility_markup():
    """Verify base template renders accessible mobile toggle and navigation structure."""
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))

    def mock_url_for(endpoint, path=""):
        return f"/static/{path}"

    env.globals["url_for"] = mock_url_for
    template = env.get_template("base.html")
    rendered = template.render()

    # Mobile menu toggle button with accessibility attributes
    assert 'class="menu-toggle"' in rendered
    assert 'aria-controls="primary-navigation"' in rendered
    assert 'aria-expanded="false"' in rendered
    assert 'aria-label="Toggle navigation menu"' in rendered

    # Accessible navigation container
    assert 'id="primary-navigation"' in rendered
    assert 'role="navigation"' in rendered
    assert 'aria-label="Primary Navigation"' in rendered

    # Script inclusion
    assert '<script src="/static/js/main.js" defer></script>' in rendered


def test_responsive_header_css_rules():
    """Verify style.css includes mobile toggle breakpoint rules and stacked layout."""
    response = client.get("/static/css/style.css")
    assert response.status_code == 200
    css = response.text

    # Desktop rule hides toggle
    assert ".menu-toggle" in css
    assert "display: none;" in css

    # Mobile breakpoint rules
    assert "@media (max-width: 768px)" in css
    assert ".site-nav--open" in css
    assert ".menu-toggle[aria-expanded=\"true\"]" in css


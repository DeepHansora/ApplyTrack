"""Focused unit and integration tests for CSRF protection and Flash message utilities.

Covers:
CSRF:
1. Successful token generation (256-bit entropy, URL-safe).
2. Successful token signing and signature validation.
3. Valid cookie and form token accepted.
4. Missing cookie rejected.
5. Missing form token rejected.
6. Tampered signature rejected.
7. Mismatched form and cookie token rejected.
8. Malformed token formats rejected.
9. Constant-time comparison behavior.
10. verify_csrf FastAPI dependency enforcement (HTTP 403 on missing/invalid, HTTP 200 on valid).
11. First visit to a form generates a CSRF token and sets the matching signed cookie in the response.
12. Subsequent form request reuses the valid CSRF cookie without regenerating.
13. Form submitted with the generated token passes CSRF verification.

Flash:
1. Setting and encoding a success flash message.
2. Retrieving and consuming a flash message from cookies.
3. Single-display consumption (message cannot be retrieved twice).
4. Tampered or invalid signature rejected.
5. Oversized message rejected (exceeding 500 characters).
6. Excessively large cookie values rejected before Base64 decoding.
7. Supported categories validated (success, error, warning, info accepted; invalid rejected).
8. Flash message displayed on next request and deleted in actual response.
9. Reloading page does not display the same flash message again.
10. Invalid or tampered flash cookies are cleared from the response.
11. Jinja2 global helpers (csrf_input, csrf_token, get_flashed_message) and render_template integration.
"""

import re
from unittest.mock import patch
import pytest
from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.testclient import TestClient

from app.config import settings
from app.core import csrf, flash
from app.core.csrf import (
    generate_csrf_token,
    get_or_create_csrf_token,
    set_csrf_cookie,
    sign_csrf_token,
    validate_csrf,
    validate_signed_token,
)
from app.core.flash import (
    ALLOWED_FLASH_CATEGORIES,
    MAX_FLASH_COOKIE_LENGTH,
    MAX_FLASH_MESSAGE_LENGTH,
    decode_flash_message,
    encode_flash_message,
    get_flash_message,
    set_flash_message,
)
from app.dependencies import verify_csrf
from app.templates import render_template, templates


# ============================================================================
# CSRF Utilities Tests
# ============================================================================


def test_csrf_token_generation_entropy_and_format():
    """Verify generated CSRF token is high-entropy, URL-safe, and unique."""
    token1 = generate_csrf_token()
    token2 = generate_csrf_token()

    assert isinstance(token1, str)
    assert len(token1) >= 40  # 32 bytes base64url encoded is ~43 chars
    assert token1 != token2


def test_csrf_signing_and_signature_validation():
    """Verify signing a raw token generates a valid signature that passes validation."""
    raw_token = generate_csrf_token()
    signed_token = sign_csrf_token(raw_token)

    assert "." in signed_token
    token_part, sig_part = signed_token.split(".", 1)
    assert token_part == raw_token
    assert len(sig_part) == 64  # SHA-256 hex digest length

    extracted = validate_signed_token(signed_token)
    assert extracted == raw_token


def test_csrf_valid_cookie_and_form_token_accepted():
    """Verify validate_csrf returns True when form token matches signed cookie token."""
    raw_token = generate_csrf_token()
    signed_cookie = sign_csrf_token(raw_token)

    assert validate_csrf(cookie_token=signed_cookie, form_token=raw_token) is True


def test_csrf_missing_cookie_rejected():
    """Verify validate_csrf rejects requests with missing or empty cookie token."""
    raw_token = generate_csrf_token()

    assert validate_csrf(cookie_token=None, form_token=raw_token) is False
    assert validate_csrf(cookie_token="", form_token=raw_token) is False


def test_csrf_missing_form_token_rejected():
    """Verify validate_csrf rejects requests with missing or empty form token."""
    raw_token = generate_csrf_token()
    signed_cookie = sign_csrf_token(raw_token)

    assert validate_csrf(cookie_token=signed_cookie, form_token=None) is False
    assert validate_csrf(cookie_token=signed_cookie, form_token="") is False


def test_csrf_tampered_signature_rejected():
    """Verify tampering with the HMAC signature causes validation failure."""
    raw_token = generate_csrf_token()
    signed_token = sign_csrf_token(raw_token)

    token_part, sig_part = signed_token.split(".", 1)
    # Flip last character of hex signature
    bad_char = "0" if sig_part[-1] != "0" else "1"
    tampered_signed = f"{token_part}.{sig_part[:-1]}{bad_char}"

    assert validate_signed_token(tampered_signed) is None
    assert validate_csrf(cookie_token=tampered_signed, form_token=raw_token) is False


def test_csrf_mismatched_form_and_cookie_token_rejected():
    """Verify form token from another session/source is rejected against authentic cookie."""
    raw_cookie_token = generate_csrf_token()
    signed_cookie = sign_csrf_token(raw_cookie_token)
    attacker_token = generate_csrf_token()

    assert validate_csrf(cookie_token=signed_cookie, form_token=attacker_token) is False


def test_csrf_malformed_token_formats_rejected():
    """Verify malformed token formats return None/False safely without raising unhandled errors."""
    malformed_inputs = [
        "notokenhere",
        "multiple.dots.in.token.string",
        ".onlysignature",
        "onlytoken.",
        "...",
        12345,  # type: ignore
        None,
    ]
    for bad in malformed_inputs:
        assert validate_signed_token(bad) is None  # type: ignore
        assert validate_csrf(bad, "some_token") is False  # type: ignore


def test_csrf_constant_time_comparison_used():
    """Verify hmac.compare_digest is called during token and signature verification."""
    raw_token = generate_csrf_token()
    signed_cookie = sign_csrf_token(raw_token)

    with patch("hmac.compare_digest", wraps=csrf.hmac.compare_digest) as mock_compare:
        result = validate_csrf(cookie_token=signed_cookie, form_token=raw_token)
        assert result is True
        # compare_digest should be called for signature verification AND token matching
        assert mock_compare.call_count >= 2


def test_csrf_dependency_enforces_protection_on_post():
    """Verify the verify_csrf dependency allows valid requests and rejects invalid ones with 403."""
    app = FastAPI()

    @app.post("/test-action")
    async def sample_action(
        token: str = Depends(verify_csrf),
        payload: str = Form(...),
    ):
        return {"status": "success", "payload": payload}

    client = TestClient(app)

    raw_token = generate_csrf_token()
    signed_cookie = sign_csrf_token(raw_token)

    # 1. Valid request -> 200 OK
    client.cookies.set(settings.CSRF_COOKIE_NAME, signed_cookie)
    res_ok = client.post(
        "/test-action",
        data={"csrf_token": raw_token, "payload": "hello"},
    )
    assert res_ok.status_code == 200
    assert res_ok.json()["status"] == "success"

    # 2. Missing cookie -> 403 Forbidden
    client.cookies.clear()
    res_no_cookie = client.post(
        "/test-action",
        data={"csrf_token": raw_token, "payload": "hello"},
    )
    assert res_no_cookie.status_code == 403
    assert "Missing CSRF cookie" in res_no_cookie.json()["detail"]

    # 3. Missing form token -> 403 Forbidden
    client.cookies.set(settings.CSRF_COOKIE_NAME, signed_cookie)
    res_no_form = client.post(
        "/test-action",
        data={"payload": "hello"},
    )
    assert res_no_form.status_code == 403
    assert "Missing CSRF form token" in res_no_form.json()["detail"]

    # 4. Tampered form token -> 403 Forbidden
    res_tampered = client.post(
        "/test-action",
        data={"csrf_token": "tampered_value", "payload": "hello"},
    )
    assert res_tampered.status_code == 403
    assert "Invalid or tampered CSRF token" in res_tampered.json()["detail"]


# ============================================================================
# Flash Message Utilities Tests
# ============================================================================


def test_flash_message_setting_and_encoding():
    """Verify flash message encoding produces a signed value with valid category and text."""
    encoded = encode_flash_message(
        message="Your account was created successfully.",
        category="success",
    )
    assert "." in encoded
    decoded = decode_flash_message(encoded)
    assert decoded is not None
    assert decoded["category"] == "success"
    assert decoded["message"] == "Your account was created successfully."


def test_flash_message_supported_categories_validated():
    """Verify supported categories are accepted and unsupported categories raise ValueError."""
    for category in ALLOWED_FLASH_CATEGORIES:
        encoded = encode_flash_message(f"Test {category}", category=category)
        decoded = decode_flash_message(encoded)
        assert decoded is not None
        assert decoded["category"] == category

    # Unsupported category raises ValueError
    with pytest.raises(ValueError) as exc_info:
        encode_flash_message("Message", category="critical_danger")
    assert "Invalid flash message category" in str(exc_info.value)


def test_flash_message_oversized_rejected():
    """Verify flash messages exceeding MAX_FLASH_MESSAGE_LENGTH are rejected."""
    oversized_text = "A" * (MAX_FLASH_MESSAGE_LENGTH + 1)
    with pytest.raises(ValueError) as exc_info:
        encode_flash_message(oversized_text, category="error")
    assert "exceeds maximum length" in str(exc_info.value)

    # Empty message is rejected
    with pytest.raises(ValueError):
        encode_flash_message("", category="info")


def test_flash_message_invalid_signature_rejected():
    """Verify tampering with a flash message signature causes decode to return None."""
    encoded = encode_flash_message("Profile updated", category="info")
    b64_part, sig_part = encoded.split(".", 1)
    bad_char = "a" if sig_part[-1] != "a" else "b"
    tampered = f"{b64_part}.{sig_part[:-1]}{bad_char}"

    assert decode_flash_message(tampered) is None


def test_flash_message_excessively_large_cookie_rejected_before_decoding():
    """Verify decode_flash_message rejects excessively large cookie values before decoding."""
    huge_cookie = "A" * (MAX_FLASH_COOKIE_LENGTH + 10)
    assert decode_flash_message(huge_cookie) is None


# ============================================================================
# End-to-End Template, CSRF, and Flash Integration Tests
# ============================================================================


@pytest.fixture
def form_test_app(monkeypatch):
    """Provide a test FastAPI application with form rendering and submission routes."""
    app = FastAPI()

    # In-memory template string with both CSRF input and flash message rendering
    test_template = templates.env.from_string(
        "<!DOCTYPE html><html><body>"
        "{% set flash = get_flashed_message() %}"
        "{% if flash %}<div id='flash-box' class='{{ flash.category }}'>{{ flash.message }}</div>{% endif %}"
        "<form method='post' action='/submit'>"
        "{{ csrf_input() }}"
        "<input type='text' name='email' value='test@example.com'>"
        "<button type='submit'>Submit</button>"
        "</form>"
        "</body></html>"
    )

    monkeypatch.setattr(templates, "get_template", lambda name: test_template)

    @app.get("/form")
    def show_form(request: Request):
        return render_template(request, "dummy_form.html")

    @app.post("/submit")
    def submit_form(
        csrf_tok: str = Depends(verify_csrf),
        email: str = Form(...),
    ):
        response = RedirectResponse(url="/form", status_code=303)
        set_flash_message(response, f"Submitted successfully for {email}", category="success")
        return response

    return app


def test_first_visit_generates_csrf_token_and_attaches_cookie(form_test_app):
    """Test 1: First visit to a form generates a CSRF token and sets the matching signed cookie in response."""
    client = TestClient(form_test_app)

    response = client.get("/form")
    assert response.status_code == 200

    # 1. Cookie is set in the HTTP response headers
    assert settings.CSRF_COOKIE_NAME in response.cookies
    signed_cookie_val = response.cookies[settings.CSRF_COOKIE_NAME]

    # 2. Extract raw token from HTML input
    match = re.search(r'name="csrf_token"\s+value="([^"]+)"', response.text)
    assert match is not None
    form_token = match.group(1)

    # 3. Verified token from cookie matches the form token
    verified_token = validate_signed_token(signed_cookie_val)
    assert verified_token == form_token


def test_subsequent_form_request_reuses_valid_csrf_cookie(form_test_app):
    """Test 2: A subsequent form request reuses the valid CSRF cookie."""
    client = TestClient(form_test_app)

    # First request
    res1 = client.get("/form")
    initial_cookie = res1.cookies[settings.CSRF_COOKIE_NAME]
    token1 = validate_signed_token(initial_cookie)

    # Second request with the established cookie
    res2 = client.get("/form")
    assert res2.status_code == 200

    match = re.search(r'name="csrf_token"\s+value="([^"]+)"', res2.text)
    assert match is not None
    token2 = match.group(1)

    # Token in form is identical to the first request
    assert token2 == token1


def test_form_submitted_with_generated_token_passes_csrf_verification(form_test_app):
    """Test 3: A form submitted with the generated token passes CSRF verification."""
    client = TestClient(form_test_app)

    # Get form
    res_get = client.get("/form")
    match = re.search(r'name="csrf_token"\s+value="([^"]+)"', res_get.text)
    assert match is not None
    token = match.group(1)

    # Submit form with the extracted token and automatic client cookie
    res_post = client.post(
        "/submit",
        data={"csrf_token": token, "email": "applicant@applytrack.com"},
        follow_redirects=False,
    )
    # Redirected (303 See Other) indicating successful CSRF verification
    assert res_post.status_code == 303


def test_flash_message_displayed_and_deleted_in_response(form_test_app):
    """Tests 4 & 5: Flash message is displayed on next request and deleted in actual response."""
    client = TestClient(form_test_app)

    # 1. Trigger form submission which sets a flash message on redirect
    res_get = client.get("/form")
    token = re.search(r'name="csrf_token"\s+value="([^"]+)"', res_get.text).group(1)

    res_post = client.post(
        "/submit",
        data={"csrf_token": token, "email": "applicant@applytrack.com"},
        follow_redirects=False,
    )
    assert settings.FLASH_COOKIE_NAME in res_post.cookies

    # 2. Follow redirect to /form: flash message must be present in HTML
    res_redirect = client.get("/form")
    assert "Submitted successfully for applicant@applytrack.com" in res_redirect.text

    # 3. Response must schedule deletion of flash cookie (Max-Age=0 / expired)
    # In Starlette TestClient, a deleted cookie has value "" or is removed from client jar
    assert (
        settings.FLASH_COOKIE_NAME not in client.cookies
        or client.cookies.get(settings.FLASH_COOKIE_NAME) == ""
    )


def test_reloading_page_does_not_display_flash_message_again(form_test_app):
    """Test 6: Reloading the page does not display the same flash message again."""
    client = TestClient(form_test_app)

    # Initial get and submit without following redirect immediately
    res_get = client.get("/form")
    token = re.search(r'name="csrf_token"\s+value="([^"]+)"', res_get.text).group(1)
    client.post(
        "/submit",
        data={"csrf_token": token, "email": "user@domain.com"},
        follow_redirects=False,
    )

    # First view shows flash
    res_view1 = client.get("/form")
    assert "Submitted successfully for user@domain.com" in res_view1.text

    # Second view (reload) must NOT show flash
    res_view2 = client.get("/form")
    assert "Submitted successfully for user@domain.com" not in res_view2.text
    assert "flash-box" not in res_view2.text


def test_invalid_flash_cookies_are_cleared(form_test_app):
    """Test 7: Invalid or tampered flash cookies are cleared from the response."""
    client = TestClient(form_test_app)

    # Set an invalid/tampered flash cookie with proper cookie scope
    client.cookies.set(
        settings.FLASH_COOKIE_NAME,
        "tampered.invalid_signature_here",
        domain="testserver.local",
        path="/",
    )

    # Visiting the form page
    response = client.get("/form")
    assert response.status_code == 200

    # No flash message was rendered
    assert "flash-box" not in response.text

    # Cookie is cleared from the response headers and client jar
    assert (
        settings.FLASH_COOKIE_NAME not in client.cookies
        or client.cookies.get(settings.FLASH_COOKIE_NAME) == ""
    )

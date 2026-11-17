"""CSRF protection utilities using signed double-submit cookies and HMAC-SHA256.

Protects state-changing forms against Cross-Site Request Forgery attacks without
requiring server-side database storage. Tokens are generated using cryptographic
randomness, signed using HMAC-SHA256 with the application SECRET_KEY, and verified
using constant-time comparisons (hmac.compare_digest) to prevent timing attacks.
"""

import hashlib
import hmac
import secrets

from starlette.requests import Request
from starlette.responses import Response

from app.config import settings


def generate_csrf_token() -> str:
    """Generate a cryptographically random CSRF token with 256 bits of entropy.

    Returns:
        A high-entropy URL-safe base64 string.
    """
    return secrets.token_urlsafe(32)


def sign_csrf_token(token: str, secret_key: str | None = None) -> str:
    """Sign a raw CSRF token using HMAC-SHA256 and the application secret key.

    Args:
        token: The raw CSRF token string to sign.
        secret_key: Optional secret key override (defaults to settings.SECRET_KEY).

    Returns:
        A string formatted as '{token}.{signature}'.

    Raises:
        ValueError: If token is empty or not a string.
    """
    if not isinstance(token, str) or not token:
        raise ValueError("CSRF token must be a non-empty string.")

    key = (secret_key or settings.SECRET_KEY).encode("utf-8")
    signature = hmac.new(key, token.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{token}.{signature}"


def validate_signed_token(signed_token: str | None, secret_key: str | None = None) -> str | None:
    """Validate a signed CSRF token cookie and extract the raw token.

    Verifies the HMAC-SHA256 signature using constant-time comparison.
    If the signature is invalid, missing, or malformed, returns None.

    Args:
        signed_token: The signed token string formatted as '{token}.{signature}'.
        secret_key: Optional secret key override (defaults to settings.SECRET_KEY).

    Returns:
        The extracted raw token if signature is valid, or None if invalid or tampered.
    """
    if not isinstance(signed_token, str) or "." not in signed_token:
        return None

    parts = signed_token.split(".", 1)
    if len(parts) != 2:
        return None

    token, signature = parts
    if not token or not signature:
        return None

    key = (secret_key or settings.SECRET_KEY).encode("utf-8")
    expected_signature = hmac.new(key, token.encode("utf-8"), hashlib.sha256).hexdigest()

    if hmac.compare_digest(signature, expected_signature):
        return token

    return None


def validate_csrf(
    cookie_token: str | None,
    form_token: str | None,
    secret_key: str | None = None,
) -> bool:
    """Validate submitted form token against the signed CSRF cookie.

    1. Validates the HMAC signature on the cookie token to ensure authenticity.
    2. Compares the submitted form token against the verified token from the cookie
       using constant-time comparison (hmac.compare_digest).

    Args:
        cookie_token: The signed token string from the request cookie.
        form_token: The raw token string submitted in the HTML form.
        secret_key: Optional secret key override.

    Returns:
        True if the form token matches the authentic cookie token; False otherwise.
    """
    if not isinstance(cookie_token, str) or not isinstance(form_token, str):
        return False

    if not cookie_token or not form_token:
        return False

    verified_token = validate_signed_token(cookie_token, secret_key=secret_key)
    if verified_token is None:
        return False

    return hmac.compare_digest(form_token, verified_token)


def set_csrf_cookie(response: Response, raw_token: str) -> None:
    """Sign a raw CSRF token and attach it as an HTTP cookie to the response.

    Uses configured cookie security parameters (Secure in production, SameSite=Lax,
    Path=/, and 24-hour expiration).

    Args:
        response: The FastAPI/Starlette Response object.
        raw_token: The raw token to sign and store in the cookie.
    """
    signed_value = sign_csrf_token(raw_token)
    response.set_cookie(
        key=settings.CSRF_COOKIE_NAME,
        value=signed_value,
        max_age=settings.CSRF_COOKIE_MAX_AGE,
        path=settings.CSRF_COOKIE_PATH,
        domain=None,
        secure=settings.CSRF_COOKIE_SECURE,
        httponly=settings.CSRF_COOKIE_HTTPONLY,
        samesite=settings.CSRF_COOKIE_SAMESITE,
    )


def get_or_create_csrf_token(request: Request, response: Response | None = None) -> str:
    """Retrieve an existing valid CSRF token from request cookies or generate a new one.

    If a new token is generated and a Response is provided, the signed cookie
    is attached to the response. Also caches the token on request.state.csrf_token.

    Args:
        request: The incoming Starlette Request.
        response: Optional Response object on which to set the cookie if generated.

    Returns:
        The raw CSRF token string to be rendered into HTML form hidden inputs.
    """
    # Check if already resolved during current request
    cached = getattr(request.state, "csrf_token", None)
    if cached:
        return cached

    # Try extracting and validating from existing cookie
    cookie_val = request.cookies.get(settings.CSRF_COOKIE_NAME)
    valid_token = validate_signed_token(cookie_val)

    if valid_token:
        request.state.csrf_token = valid_token
        request.state.csrf_cookie_needed = False
        return valid_token

    # Generate new token
    new_token = generate_csrf_token()
    request.state.csrf_token = new_token
    request.state.csrf_cookie_needed = True

    if response is not None:
        set_csrf_cookie(response, new_token)
        request.state.csrf_cookie_needed = False

    return new_token

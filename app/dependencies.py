"""FastAPI dependencies for request handling, authentication, and resource management."""

from typing import Generator
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.config import settings
from app.core.csrf import validate_csrf
from app.database import SessionLocal
from app.models.user import User
from app.services.auth_service import AuthService, InvalidSessionError


def get_db() -> Generator[Session, None, None]:
    """Provide a transactional database session per request.

    Yields an active SQLAlchemy Session. Ensures the session is unconditionally
    closed in a finally block when the request completes or an error occurs.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
) -> User | None:
    """Retrieve the currently authenticated user from the session cookie.

    Reads the raw session token from the HttpOnly session cookie, validates it
    against the database via AuthService (checking absolute expiration, idle
    timeout, revocation, and active user status).

    Returns:
        The authenticated User instance if the session is valid, or None if
        missing, expired, revoked, inactive, or malformed.
    """
    raw_token = request.cookies.get(settings.SESSION_COOKIE_NAME)
    if not raw_token:
        return None

    auth_service = AuthService(db)
    try:
        return auth_service.validate_session(raw_token)
    except InvalidSessionError:
        return None
    except Exception:
        # Prevent exposing internal database errors or sensitive information
        return None


def require_authenticated_user(
    current_user: User | None = Depends(get_current_user),
) -> User:
    """Require an active authenticated user.

    If no user is authenticated (missing, invalid, or expired session),
    halts request processing and redirects the client to /login using HTTP 303.
    Otherwise, returns the authenticated User instance.

    Args:
        current_user: The User resolved from get_current_user.

    Returns:
        The authenticated User instance.

    Raises:
        HTTPException: HTTP 303 See Other redirecting to /login if unauthenticated.
    """
    if current_user is None:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/login"},
        )
    return current_user


async def verify_csrf(request: Request) -> str:
    """Validate CSRF protection for state-changing requests.

    Reads the signed CSRF cookie and the submitted form token.
    Validates both using cryptographic HMAC signature verification and
    constant-time comparison. Rejects missing, invalid, or tampered tokens
    with HTTP 403 Forbidden.

    Works for both authenticated and anonymous users. Does not require
    a database connection.

    Args:
        request: The incoming Starlette Request.

    Returns:
        The validated CSRF token string.

    Raises:
        HTTPException: HTTP 403 Forbidden if CSRF verification fails.
    """
    cookie_token = request.cookies.get(settings.CSRF_COOKIE_NAME)
    if not cookie_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF verification failed: Missing CSRF cookie.",
        )

    try:
        form = await request.form()
        form_token = form.get("csrf_token")
    except Exception:
        form_token = None

    if not form_token or not isinstance(form_token, str):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF verification failed: Missing CSRF form token.",
        )

    if not validate_csrf(cookie_token=cookie_token, form_token=form_token):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF verification failed: Invalid or tampered CSRF token.",
        )

    return form_token

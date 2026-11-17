"""FastAPI dependencies for request handling and resource management."""

from typing import Generator
from fastapi import HTTPException, Request, status
from sqlalchemy.orm import Session

from app.config import settings
from app.core.csrf import validate_csrf
from app.database import SessionLocal


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

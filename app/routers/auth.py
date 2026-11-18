"""Authentication router for user registration, login, and logout."""

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.config import settings
from app.core.flash import set_flash_message
from app.dependencies import get_current_user, get_db, verify_csrf
from app.models.user import User
from app.schemas.user import UserCreate
from app.services.auth_service import (
    AuthService,
    DuplicateEmailError,
    InvalidCredentialsError,
)
from app.templates import templates

router = APIRouter(tags=["auth"])


@router.get(
    "/register",
    response_class=HTMLResponse,
    summary="Registration Page",
    response_description="Renders user registration form",
)
def register_page(
    request: Request,
    current_user: User | None = Depends(get_current_user),
):
    """Display user registration form."""
    if current_user is not None:
        return RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)

    return templates.TemplateResponse(
        request=request,
        name="auth/register.html",
    )


@router.post(
    "/register",
    summary="Submit Registration",
    response_description="Redirects to login page on completion via PRG",
    dependencies=[Depends(verify_csrf)],
)
async def register(
    request: Request,
    full_name: str = Form(""),
    email: str = Form(""),
    password: str = Form(""),
    ai_consent: bool = Form(False),
    db: Session = Depends(get_db),
):
    """Handle user registration form submission with PRG flow.

    Validates submitted form fields against the UserCreate Pydantic schema.
    If valid, creates the user account via AuthService. Suppresses duplicate
    email errors to eliminate account enumeration risks.
    """
    try:
        user_in = UserCreate(
            full_name=full_name,
            email=email,
            password=password,
            ai_consent=ai_consent,
        )
    except (ValidationError, ValueError) as exc:
        error_msg = exc.errors()[0]["msg"] if hasattr(exc, "errors") else str(exc)
        response = RedirectResponse(url="/register", status_code=status.HTTP_303_SEE_OTHER)
        set_flash_message(response, error_msg, category="error")
        return response

    auth_service = AuthService(db)
    try:
        auth_service.register(user_in=user_in)
    except DuplicateEmailError:
        # Prevent account enumeration: do not reveal whether the email already exists
        pass
    except Exception:
        response = RedirectResponse(url="/register", status_code=status.HTTP_303_SEE_OTHER)
        set_flash_message(response, "An unexpected error occurred. Please try again.", category="error")
        return response

    response = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    set_flash_message(response, "Registration successful. Please log in.", category="success")
    return response


@router.get(
    "/login",
    response_class=HTMLResponse,
    summary="Login Page",
    response_description="Renders user login form",
)
def login_page(
    request: Request,
    current_user: User | None = Depends(get_current_user),
):
    """Display user login form."""
    if current_user is not None:
        return RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)

    return templates.TemplateResponse(
        request=request,
        name="auth/login.html",
    )


@router.post(
    "/login",
    summary="Submit Login Credentials",
    response_description="Redirects to dashboard with session cookie on success via PRG",
    dependencies=[Depends(verify_csrf)],
)
async def login(
    request: Request,
    email: str = Form(""),
    password: str = Form(""),
    db: Session = Depends(get_db),
):
    """Handle user login credentials with PRG flow.

    Verifies credentials using timing-attack resistant password verification.
    On success, creates a database-backed session, issues the raw token in an
    HttpOnly cookie, and redirects to /dashboard.
    """
    if not email.strip() or not password:
        response = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
        set_flash_message(response, "Invalid email or password.", category="error")
        return response

    auth_service = AuthService(db)
    try:
        user = auth_service.authenticate(email=email, password=password)
    except InvalidCredentialsError:
        response = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
        set_flash_message(response, "Invalid email or password.", category="error")
        return response
    except Exception:
        response = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
        set_flash_message(response, "Invalid email or password.", category="error")
        return response

    raw_token = auth_service.create_session(user.id)

    response = RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        key=settings.SESSION_COOKIE_NAME,
        value=raw_token,
        max_age=settings.SESSION_ABSOLUTE_LIFETIME_HOURS * 3600,
        path=settings.SESSION_COOKIE_PATH,
        domain=None,
        secure=settings.SESSION_COOKIE_SECURE,
        httponly=settings.SESSION_COOKIE_HTTPONLY,
        samesite=settings.SESSION_COOKIE_SAMESITE,
    )
    return response


@router.post(
    "/logout",
    summary="User Logout",
    response_description="Invalidates active session, clears cookie, and redirects to login",
    dependencies=[Depends(verify_csrf)],
)
async def logout(
    request: Request,
    db: Session = Depends(get_db),
):
    """Invalidate session and log out the user."""
    raw_token = request.cookies.get(settings.SESSION_COOKIE_NAME)
    if raw_token:
        auth_service = AuthService(db)
        auth_service.logout(raw_token)

    response = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(
        key=settings.SESSION_COOKIE_NAME,
        path=settings.SESSION_COOKIE_PATH,
        domain=None,
    )
    set_flash_message(response, "You have been logged out.", category="info")
    return response

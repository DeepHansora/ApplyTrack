"""Template engine configuration for server-rendered HTML pages."""

from pathlib import Path
from typing import Any, Mapping

from fastapi.templating import Jinja2Templates
from jinja2 import pass_context
from markupsafe import Markup
from starlette.background import BackgroundTasks
from starlette.requests import Request
from starlette.responses import Response

from app.config import settings
from app.core.csrf import get_or_create_csrf_token, set_csrf_cookie
from app.core.flash import get_flash_message

# Calculate absolute path to app/templates
BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"


def _attach_security_and_flash_cookies(request: Request, response: Response) -> None:
    """Attach pending CSRF cookies and clear consumed/invalid flash cookies."""
    # 1. Attach CSRF cookie if a new token was generated for an HTML form
    if getattr(request.state, "csrf_cookie_needed", False):
        token = getattr(request.state, "csrf_token", None)
        if token:
            set_csrf_cookie(response, token)
            request.state.csrf_cookie_needed = False

    # 2. Clear flash cookie if present on request (consumed or invalid/tampered)
    if settings.FLASH_COOKIE_NAME in request.cookies or getattr(request.state, "flash_cookie_cleared", False):
        response.delete_cookie(
            key=settings.FLASH_COOKIE_NAME,
            path=settings.FLASH_COOKIE_PATH,
            domain=None,
        )


class ApplyTrackTemplates(Jinja2Templates):
    """Jinja2Templates subclass that automatically attaches CSRF and clears Flash cookies."""

    def TemplateResponse(self, *args: Any, **kwargs: Any) -> Response:
        response = super().TemplateResponse(*args, **kwargs)

        # Extract request from kwargs or args
        request: Request | None = kwargs.get("request")
        if not request:
            context = kwargs.get("context")
            if isinstance(context, dict):
                request = context.get("request")
        if not request and args:
            for arg in args:
                if isinstance(arg, Request):
                    request = arg
                    break
                elif isinstance(arg, dict) and "request" in arg:
                    request = arg["request"]
                    break

        if request is not None and isinstance(request, Request):
            _attach_security_and_flash_cookies(request, response)

        return response


# Jinja2 template renderer instance
templates = ApplyTrackTemplates(directory=str(TEMPLATES_DIR))


def render_template(
    request: Request,
    name: str,
    context: dict[str, Any] | None = None,
    status_code: int = 200,
    headers: Mapping[str, str] | None = None,
    media_type: str | None = None,
    background: BackgroundTasks | None = None,
) -> Response:
    """Render a Jinja2 template and attach necessary CSRF/Flash cookies to the response.

    Simple, beginner-friendly helper function for route handlers.

    Args:
        request: The incoming Starlette Request.
        name: The template file path relative to app/templates.
        context: Optional dictionary of variables to inject into the template.
        status_code: HTTP status code (defaults to 200).
        headers: Optional HTTP headers dictionary.
        media_type: Optional media type override.
        background: Optional background tasks.

    Returns:
        The rendered Response with attached CSRF and cleared Flash cookies.
    """
    ctx = dict(context) if context else {}
    return templates.TemplateResponse(
        request=request,
        name=name,
        context=ctx,
        status_code=status_code,
        headers=headers,
        media_type=media_type,
        background=background,
    )


@pass_context
def csrf_token_global(context: dict) -> str:
    """Jinja2 global function returning the raw CSRF token for the current request."""
    request = context.get("request")
    if request:
        return get_or_create_csrf_token(request)
    return ""


@pass_context
def csrf_input_global(context: dict) -> Markup:
    """Jinja2 global function rendering the hidden CSRF input field for HTML forms."""
    token = csrf_token_global(context)
    return Markup(f'<input type="hidden" name="csrf_token" value="{token}">')


@pass_context
def flash_message_global(context: dict) -> dict[str, str] | None:
    """Jinja2 global function returning the active flash message dictionary if present."""
    request = context.get("request")
    if request:
        return get_flash_message(request)
    return None


# Register Jinja2 global template helpers
templates.env.globals["csrf_token"] = csrf_token_global
templates.env.globals["csrf_input"] = csrf_input_global
templates.env.globals["get_flashed_message"] = flash_message_global
templates.env.globals["str"] = str

"""Dashboard router for authenticated user workspace."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.dependencies import require_authenticated_user
from app.models.user import User
from app.templates import templates

router = APIRouter(tags=["dashboard"])


@router.get(
    "/dashboard",
    response_class=HTMLResponse,
    summary="User Dashboard",
    response_description="Renders the user dashboard workspace",
)
def dashboard(
    request: Request,
    user: User = Depends(require_authenticated_user),
):
    """Render the user dashboard workspace.

    Protected route requiring an active authenticated user session.
    Derives identity exclusively from the validated session, never from URL parameters.
    """
    return templates.TemplateResponse(
        request=request,
        name="dashboard/index.html",
        context={"user": user},
    )

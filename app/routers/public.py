"""Public routes for application status, health checks, and landing pages."""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.dependencies import get_db
from app.templates import templates

router = APIRouter(tags=["public"])


@router.get(
    "/",
    response_class=HTMLResponse,
    summary="Landing Page",
    response_description="Renders the ApplyTrack editorial landing page",
)
def landing_page(request: Request):
    """Render the public ApplyTrack landing page."""
    return templates.TemplateResponse(
        request=request,
        name="public/landing.html",
        context={"current_year": 2026},
    )


@router.get(
    "/health",
    status_code=status.HTTP_200_OK,
    summary="Service Health Check",
    response_description="Service and database health status",
)
def health_check(db: Session = Depends(get_db)):
    """Verify application runtime and database connectivity.

    Executes a lightweight query against PostgreSQL. Returns HTTP 200 if the
    database is responsive, or HTTP 503 Service Unavailable if unreachable,
    without leaking internal connection details or credentials.
    """
    try:
        db.execute(text("SELECT 1"))
        return {
            "status": "healthy",
            "database": "connected",
        }
    except SQLAlchemyError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service unavailable: database connection check failed",
        )

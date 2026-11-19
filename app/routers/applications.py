"""Application router for user job applications, listing, creation, and detail views."""

from datetime import datetime, timezone
from decimal import Decimal
import math
from typing import Any, Optional
from urllib.parse import urlencode
import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.flash import set_flash_message
from app.dependencies import get_db, require_authenticated_user, verify_csrf
from app.models.application import APPLICATION_STATUSES, EMPLOYMENT_TYPES
from app.models.user import User
from app.schemas.application import ApplicationCreate, ApplicationUpdate
from app.services.application_service import (
    ApplicationNotFoundError,
    ApplicationService,
    ApplicationServiceError,
    InvalidApplicationCompanyError,
    InvalidApplicationUpdateError,
    InvalidSalaryRangeError,
)
from app.services.company_service import CompanyService
from app.templates import templates

router = APIRouter(tags=["applications"])


def _parse_form_date(date_str: Optional[str]) -> Optional[datetime]:
    """Parse HTML date input (YYYY-MM-DD) into a timezone-aware UTC datetime."""
    if not date_str or not date_str.strip():
        return None
    trimmed = date_str.strip()
    try:
        dt = datetime.strptime(trimmed, "%Y-%m-%d")
        return dt.replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    try:
        dt = datetime.fromisoformat(trimmed)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        raise ValueError("Invalid date format. Expected YYYY-MM-DD.")


def _parse_form_decimal(val: Optional[str]) -> Optional[Decimal]:
    """Parse form input string into a Decimal, or return None if blank."""
    if val is None or not str(val).strip():
        return None
    try:
        return Decimal(str(val).strip())
    except Exception:
        raise ValueError("Salary must be a valid number.")


def _parse_query_uuid(uuid_str: Optional[str]) -> Optional[uuid.UUID]:
    """Parse query UUID string, converting empty or whitespace-only to None and validating format."""
    if uuid_str is None or not uuid_str.strip():
        return None
    try:
        return uuid.UUID(uuid_str.strip())
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid company ID format.",
        )


@router.get(
    "/applications",
    response_class=HTMLResponse,
    summary="List Applications",
    response_description="Renders user's job applications directory with search, filters, sorting and pagination",
)
def list_applications(
    request: Request,
    search: Optional[str] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    company_id: Optional[str] = Query(None),
    employment_type: Optional[str] = Query(None),
    applied_after: Optional[str] = Query(None),
    applied_before: Optional[str] = Query(None),
    archive_status: str = Query("active"),
    include_archived: bool = Query(False),
    sort_by: str = Query("created_at"),
    sort_order: str = Query("desc"),
    page: int = Query(1, ge=1),
    per_page: int = Query(20),
    user: User = Depends(require_authenticated_user),
    db: Session = Depends(get_db),
):
    """List applications belonging to the authenticated user with search, filter, and pagination."""
    app_service = ApplicationService(db)
    company_service = CompanyService(db)

    # Active companies list for dropdown filter
    user_companies = company_service.list_companies(user_id=user.id, is_archived=False)

    # Resolve company filter (empty or whitespace string becomes None)
    company_uuid = _parse_query_uuid(company_id)

    # Resolve archive status filter
    norm_archive = archive_status.strip().lower() if archive_status else "active"
    if norm_archive == "archived":
        is_archived_val = True
    elif norm_archive == "all" or include_archived:
        is_archived_val = None
    else:
        is_archived_val = False

    # Parse date filters if provided
    applied_after_dt = None
    applied_before_dt = None
    date_error = None
    try:
        applied_after_dt = _parse_form_date(applied_after)
        applied_before_dt = _parse_form_date(applied_before)
    except ValueError as exc:
        date_error = str(exc)

    result: dict[str, Any] = {
        "items": [],
        "total": 0,
        "page": page,
        "per_page": per_page,
        "total_pages": 1,
    }

    if not date_error:
        try:
            result = app_service.list_applications(
                user_id=user.id,
                search=search,
                status=status_filter,
                company_id=company_uuid,
                employment_type=employment_type,
                applied_after=applied_after_dt,
                applied_before=applied_before_dt,
                is_archived=is_archived_val,
                sort_by=sort_by,
                sort_order=sort_order,
                page=page,
                per_page=per_page,
            )
        except ApplicationServiceError as exc:
            date_error = str(exc)

    def build_page_url(target_page: int) -> str:
        """Construct pagination URL preserving all current search and filter parameters."""
        params = dict(request.query_params)
        params["page"] = str(target_page)
        return f"/applications?{urlencode(params)}"

    return templates.TemplateResponse(
        request=request,
        name="applications/list.html",
        context={
            "user": user,
            "applications": result["items"],
            "total": result["total"],
            "page": result["page"],
            "per_page": result["per_page"],
            "total_pages": result["total_pages"],
            "companies": user_companies,
            "statuses": APPLICATION_STATUSES,
            "employment_types": EMPLOYMENT_TYPES,
            "search": search or "",
            "selected_status": status_filter or "",
            "selected_company_id": str(company_uuid) if company_uuid else "",
            "selected_employment_type": employment_type or "",
            "applied_after": applied_after or "",
            "applied_before": applied_before or "",
            "archive_status": norm_archive,
            "include_archived": include_archived,
            "sort_by": sort_by,
            "sort_order": sort_order,
            "build_page_url": build_page_url,
            "error": date_error,
        },
    )


@router.get(
    "/applications/new",
    response_class=HTMLResponse,
    summary="New Application Form",
    response_description="Renders blank application creation form",
)
def new_application_page(
    request: Request,
    user: User = Depends(require_authenticated_user),
    db: Session = Depends(get_db),
):
    """Display blank application creation form."""
    company_service = CompanyService(db)
    active_companies = company_service.list_companies(user_id=user.id, is_archived=False)

    return templates.TemplateResponse(
        request=request,
        name="applications/form.html",
        context={
            "user": user,
            "application": None,
            "companies": active_companies,
            "statuses": APPLICATION_STATUSES,
            "employment_types": EMPLOYMENT_TYPES,
            "form_data": {"status": "Saved", "currency": "USD"},
            "error": None,
        },
    )


@router.post(
    "/applications",
    summary="Create Application",
    response_description="Creates application and redirects to detail page via PRG",
    dependencies=[Depends(verify_csrf)],
)
async def create_application(
    request: Request,
    company_id: Optional[str] = Form(None),
    job_title: str = Form(""),
    job_description: Optional[str] = Form(None),
    job_url: Optional[str] = Form(None),
    employment_type: Optional[str] = Form(None),
    location: Optional[str] = Form(None),
    salary_min: Optional[str] = Form(None),
    salary_max: Optional[str] = Form(None),
    currency: Optional[str] = Form("USD"),
    status_field: str = Form("Saved", alias="status"),
    applied_at: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
    user: User = Depends(require_authenticated_user),
    db: Session = Depends(get_db),
):
    """Handle application creation form submission with PRG flow."""
    company_service = CompanyService(db)
    active_companies = company_service.list_companies(user_id=user.id, is_archived=False)

    raw_form = {
        "company_id": company_id or "",
        "job_title": job_title,
        "job_description": job_description or "",
        "job_url": job_url or "",
        "employment_type": employment_type or "",
        "location": location or "",
        "salary_min": salary_min or "",
        "salary_max": salary_max or "",
        "currency": currency or "USD",
        "status": status_field or "Saved",
        "applied_at": applied_at or "",
        "notes": notes or "",
    }

    # Validate company_id UUID format
    if not company_id or not company_id.strip():
        return templates.TemplateResponse(
            request=request,
            name="applications/form.html",
            context={
                "user": user,
                "application": None,
                "companies": active_companies,
                "statuses": APPLICATION_STATUSES,
                "employment_types": EMPLOYMENT_TYPES,
                "form_data": raw_form,
                "error": "Please select a valid company.",
            },
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    try:
        company_uuid = uuid.UUID(company_id.strip())
    except ValueError:
        return templates.TemplateResponse(
            request=request,
            name="applications/form.html",
            context={
                "user": user,
                "application": None,
                "companies": active_companies,
                "statuses": APPLICATION_STATUSES,
                "employment_types": EMPLOYMENT_TYPES,
                "form_data": raw_form,
                "error": "Invalid company ID format.",
            },
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    # Parse and validate numeric and date fields
    try:
        parsed_salary_min = _parse_form_decimal(salary_min)
        parsed_salary_max = _parse_form_decimal(salary_max)
        parsed_applied_at = _parse_form_date(applied_at)
    except ValueError as exc:
        return templates.TemplateResponse(
            request=request,
            name="applications/form.html",
            context={
                "user": user,
                "application": None,
                "companies": active_companies,
                "statuses": APPLICATION_STATUSES,
                "employment_types": EMPLOYMENT_TYPES,
                "form_data": raw_form,
                "error": str(exc),
            },
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    # Validate against ApplicationCreate schema
    try:
        app_in = ApplicationCreate(
            company_id=company_uuid,
            job_title=job_title,
            job_description=job_description,
            job_url=job_url,
            employment_type=employment_type,
            location=location,
            salary_min=parsed_salary_min,
            salary_max=parsed_salary_max,
            currency=currency or "USD",
            status=status_field or "Saved",
            applied_at=parsed_applied_at,
            notes=notes,
        )
    except (ValidationError, ValueError) as exc:
        error_msg = exc.errors()[0]["msg"] if hasattr(exc, "errors") else str(exc)
        if error_msg.startswith("Value error, "):
            error_msg = error_msg[len("Value error, "):]
        return templates.TemplateResponse(
            request=request,
            name="applications/form.html",
            context={
                "user": user,
                "application": None,
                "companies": active_companies,
                "statuses": APPLICATION_STATUSES,
                "employment_types": EMPLOYMENT_TYPES,
                "form_data": raw_form,
                "error": error_msg,
            },
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    app_service = ApplicationService(db)
    try:
        new_app = app_service.create_application(user_id=user.id, application_in=app_in)
    except (InvalidApplicationCompanyError, InvalidSalaryRangeError) as exc:
        return templates.TemplateResponse(
            request=request,
            name="applications/form.html",
            context={
                "user": user,
                "application": None,
                "companies": active_companies,
                "statuses": APPLICATION_STATUSES,
                "employment_types": EMPLOYMENT_TYPES,
                "form_data": raw_form,
                "error": str(exc),
            },
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    response = RedirectResponse(
        url=f"/applications/{new_app.id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )
    set_flash_message(
        response,
        f"Application for '{new_app.job_title}' created successfully.",
        category="success",
    )
    return response


@router.get(
    "/applications/{application_id}",
    response_class=HTMLResponse,
    summary="Application Detail",
    response_description="Renders full application details",
)
def get_application(
    request: Request,
    application_id: uuid.UUID,
    user: User = Depends(require_authenticated_user),
    db: Session = Depends(get_db),
):
    """Display details for the specified application. Returns 404 for unowned applications."""
    app_service = ApplicationService(db)
    try:
        application = app_service.get_application(application_id=application_id, user_id=user.id)
    except ApplicationNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Application not found.",
        )

    return templates.TemplateResponse(
        request=request,
        name="applications/detail.html",
        context={
            "user": user,
            "application": application,
        },
    )


@router.get(
    "/applications/{application_id}/edit",
    response_class=HTMLResponse,
    summary="Edit Application Form",
    response_description="Renders pre-filled application edit form",
)
def edit_application_page(
    request: Request,
    application_id: uuid.UUID,
    user: User = Depends(require_authenticated_user),
    db: Session = Depends(get_db),
):
    """Display pre-filled application edit form."""
    app_service = ApplicationService(db)
    try:
        application = app_service.get_application(application_id=application_id, user_id=user.id)
    except ApplicationNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Application not found.",
        )

    company_service = CompanyService(db)
    active_companies = company_service.list_companies(user_id=user.id, is_archived=False)

    # Preserve existing company in selector even if that company was subsequently archived
    if application.company and application.company.is_archived:
        companies_list = [application.company] + [c for c in active_companies if c.id != application.company_id]
    else:
        companies_list = active_companies

    applied_at_str = application.applied_at.strftime("%Y-%m-%d") if application.applied_at else ""
    form_data = {
        "company_id": str(application.company_id),
        "job_title": application.job_title,
        "job_description": application.job_description or "",
        "job_url": application.job_url or "",
        "employment_type": application.employment_type or "",
        "location": application.location or "",
        "salary_min": str(application.salary_min) if application.salary_min is not None else "",
        "salary_max": str(application.salary_max) if application.salary_max is not None else "",
        "currency": application.currency or "USD",
        "applied_at": applied_at_str,
        "notes": application.notes or "",
    }

    return templates.TemplateResponse(
        request=request,
        name="applications/form.html",
        context={
            "user": user,
            "application": application,
            "companies": companies_list,
            "employment_types": EMPLOYMENT_TYPES,
            "form_data": form_data,
            "error": None,
        },
    )


@router.post(
    "/applications/{application_id}/edit",
    summary="Submit Application Edit",
    response_description="Updates application and redirects to detail page via PRG",
    dependencies=[Depends(verify_csrf)],
)
async def edit_application(
    request: Request,
    application_id: uuid.UUID,
    company_id: Optional[str] = Form(None),
    job_title: str = Form(""),
    job_description: Optional[str] = Form(None),
    job_url: Optional[str] = Form(None),
    employment_type: Optional[str] = Form(None),
    location: Optional[str] = Form(None),
    salary_min: Optional[str] = Form(None),
    salary_max: Optional[str] = Form(None),
    currency: Optional[str] = Form(None),
    applied_at: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
    user: User = Depends(require_authenticated_user),
    db: Session = Depends(get_db),
):
    """Handle application edit form submission with PRG flow."""
    app_service = ApplicationService(db)
    try:
        application = app_service.get_application(application_id=application_id, user_id=user.id)
    except ApplicationNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Application not found.",
        )

    company_service = CompanyService(db)
    active_companies = company_service.list_companies(user_id=user.id, is_archived=False)
    if application.company and application.company.is_archived:
        companies_list = [application.company] + [c for c in active_companies if c.id != application.company_id]
    else:
        companies_list = active_companies

    raw_form = {
        "company_id": company_id or str(application.company_id),
        "job_title": job_title,
        "job_description": job_description or "",
        "job_url": job_url or "",
        "employment_type": employment_type or "",
        "location": location or "",
        "salary_min": salary_min or "",
        "salary_max": salary_max or "",
        "currency": currency or application.currency or "USD",
        "applied_at": applied_at or "",
        "notes": notes or "",
    }

    # Parse company UUID if provided
    company_uuid = None
    if company_id and company_id.strip():
        try:
            company_uuid = uuid.UUID(company_id.strip())
        except ValueError:
            return templates.TemplateResponse(
                request=request,
                name="applications/form.html",
                context={
                    "user": user,
                    "application": application,
                    "companies": companies_list,
                    "employment_types": EMPLOYMENT_TYPES,
                    "form_data": raw_form,
                    "error": "Invalid company ID format.",
                },
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

    # Parse numeric and date fields
    try:
        parsed_salary_min = _parse_form_decimal(salary_min)
        parsed_salary_max = _parse_form_decimal(salary_max)
        parsed_applied_at = _parse_form_date(applied_at)
    except ValueError as exc:
        return templates.TemplateResponse(
            request=request,
            name="applications/form.html",
            context={
                "user": user,
                "application": application,
                "companies": companies_list,
                "employment_types": EMPLOYMENT_TYPES,
                "form_data": raw_form,
                "error": str(exc),
            },
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    # Build update kwargs with only explicitly provided fields
    update_kwargs: dict[str, Any] = {
        "job_title": job_title,
        "job_description": job_description,
        "job_url": job_url,
        "employment_type": employment_type,
        "location": location,
        "salary_min": parsed_salary_min,
        "salary_max": parsed_salary_max,
        "currency": currency,
        "applied_at": parsed_applied_at,
        "notes": notes,
    }
    if company_uuid is not None:
        update_kwargs["company_id"] = company_uuid

    try:
        update_in = ApplicationUpdate(**update_kwargs)
    except (ValidationError, ValueError) as exc:
        error_msg = exc.errors()[0]["msg"] if hasattr(exc, "errors") else str(exc)
        if error_msg.startswith("Value error, "):
            error_msg = error_msg[len("Value error, "):]
        return templates.TemplateResponse(
            request=request,
            name="applications/form.html",
            context={
                "user": user,
                "application": application,
                "companies": companies_list,
                "employment_types": EMPLOYMENT_TYPES,
                "form_data": raw_form,
                "error": error_msg,
            },
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    try:
        updated_app = app_service.update_application(
            application_id=application_id,
            user_id=user.id,
            application_in=update_in,
        )
    except (InvalidApplicationCompanyError, InvalidSalaryRangeError, InvalidApplicationUpdateError) as exc:
        return templates.TemplateResponse(
            request=request,
            name="applications/form.html",
            context={
                "user": user,
                "application": application,
                "companies": companies_list,
                "employment_types": EMPLOYMENT_TYPES,
                "form_data": raw_form,
                "error": str(exc),
            },
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    response = RedirectResponse(
        url=f"/applications/{updated_app.id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )
    set_flash_message(
        response,
        f"Application for '{updated_app.job_title}' updated successfully.",
        category="success",
    )
    return response


@router.post(
    "/applications/{application_id}/archive",
    summary="Archive Application",
    response_description="Archives application and redirects to applications list via PRG",
    dependencies=[Depends(verify_csrf)],
)
async def archive_application(
    application_id: uuid.UUID,
    user: User = Depends(require_authenticated_user),
    db: Session = Depends(get_db),
):
    """Mark an owned application as archived (soft-deleted). Returns 404 for unowned applications."""
    app_service = ApplicationService(db)
    try:
        app = app_service.archive_application(application_id=application_id, user_id=user.id)
    except ApplicationNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Application not found.",
        )

    response = RedirectResponse(
        url="/applications",
        status_code=status.HTTP_303_SEE_OTHER,
    )
    set_flash_message(
        response,
        f"Application for '{app.job_title}' archived.",
        category="info",
    )
    return response


@router.post(
    "/applications/{application_id}/unarchive",
    summary="Unarchive Application",
    response_description="Restores application from archive and redirects to detail page via PRG",
    dependencies=[Depends(verify_csrf)],
)
async def unarchive_application(
    application_id: uuid.UUID,
    user: User = Depends(require_authenticated_user),
    db: Session = Depends(get_db),
):
    """Restore an archived application owned by the user."""
    app_service = ApplicationService(db)
    try:
        app = app_service.unarchive_application(application_id=application_id, user_id=user.id)
    except ApplicationNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Application not found.",
        )

    response = RedirectResponse(
        url=f"/applications/{app.id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )
    set_flash_message(
        response,
        f"Application for '{app.job_title}' unarchived.",
        category="success",
    )
    return response

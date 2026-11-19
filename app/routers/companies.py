"""Company router for user-owned organization directory management."""

from typing import Optional
import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.flash import set_flash_message
from app.dependencies import get_db, require_authenticated_user, verify_csrf
from app.models.user import User
from app.schemas.company import CompanyCreate, CompanyUpdate
from app.services.company_service import (
    CompanyNotFoundError,
    CompanyService,
    DuplicateCompanyNameError,
)
from app.templates import templates

router = APIRouter(tags=["companies"])


@router.get(
    "/companies",
    response_class=HTMLResponse,
    summary="List Companies",
    response_description="Renders user's company directory",
)
def list_companies(
    request: Request,
    include_archived: bool = False,
    user: User = Depends(require_authenticated_user),
    db: Session = Depends(get_db),
):
    """List companies belonging to the authenticated user.

    Excludes archived companies by default unless include_archived is True.
    Identity is strictly derived from the validated session.
    """
    service = CompanyService(db)
    is_archived_filter = None if include_archived else False
    companies = service.list_companies(user_id=user.id, is_archived=is_archived_filter)

    return templates.TemplateResponse(
        request=request,
        name="companies/list.html",
        context={
            "user": user,
            "companies": companies,
            "include_archived": include_archived,
        },
    )


@router.get(
    "/companies/new",
    response_class=HTMLResponse,
    summary="New Company Form",
    response_description="Renders blank company creation form",
)
def new_company_page(
    request: Request,
    user: User = Depends(require_authenticated_user),
):
    """Display blank company creation form."""
    return templates.TemplateResponse(
        request=request,
        name="companies/form.html",
        context={
            "user": user,
            "company": None,
            "form_data": {},
            "error": None,
        },
    )


@router.post(
    "/companies",
    summary="Create Company",
    response_description="Creates company record and redirects to detail page via PRG",
    dependencies=[Depends(verify_csrf)],
)
async def create_company(
    request: Request,
    name: str = Form(""),
    website: Optional[str] = Form(None),
    industry: Optional[str] = Form(None),
    location: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
    user: User = Depends(require_authenticated_user),
    db: Session = Depends(get_db),
):
    """Handle company creation form submission with PRG flow.

    Validates submitted inputs against CompanyCreate schema. Normalizes company name.
    If validation or duplicate error occurs, re-renders form with error and status 422.
    On success, redirects to the company detail page.
    """
    raw_form = {
        "name": name,
        "website": website or "",
        "industry": industry or "",
        "location": location or "",
        "notes": notes or "",
    }

    try:
        company_in = CompanyCreate(
            name=name,
            website=website,
            industry=industry,
            location=location,
            notes=notes,
        )
    except (ValidationError, ValueError) as exc:
        error_msg = exc.errors()[0]["msg"] if hasattr(exc, "errors") else str(exc)
        if error_msg.startswith("Value error, "):
            error_msg = error_msg[len("Value error, "):]
        return templates.TemplateResponse(
            request=request,
            name="companies/form.html",
            context={
                "user": user,
                "company": None,
                "form_data": raw_form,
                "error": error_msg,
            },
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    service = CompanyService(db)
    try:
        company = service.create_company(user_id=user.id, company_in=company_in)
    except DuplicateCompanyNameError as exc:
        return templates.TemplateResponse(
            request=request,
            name="companies/form.html",
            context={
                "user": user,
                "company": None,
                "form_data": raw_form,
                "error": str(exc),
            },
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    response = RedirectResponse(
        url=f"/companies/{company.id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )
    set_flash_message(
        response,
        f"Company '{company.name}' created successfully.",
        category="success",
    )
    return response


@router.get(
    "/companies/{id}",
    response_class=HTMLResponse,
    summary="Company Detail",
    response_description="Renders company details and associated applications",
)
def get_company(
    request: Request,
    id: uuid.UUID,
    user: User = Depends(require_authenticated_user),
    db: Session = Depends(get_db),
):
    """Display company details for the specified company.

    Returns 404 if company does not exist or belongs to another user.
    """
    service = CompanyService(db)
    try:
        company = service.get_company(company_id=id, user_id=user.id)
    except CompanyNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Company not found.",
        )

    return templates.TemplateResponse(
        request=request,
        name="companies/detail.html",
        context={
            "user": user,
            "company": company,
        },
    )


@router.get(
    "/companies/{id}/edit",
    response_class=HTMLResponse,
    summary="Edit Company Form",
    response_description="Renders pre-filled company edit form",
)
def edit_company_page(
    request: Request,
    id: uuid.UUID,
    user: User = Depends(require_authenticated_user),
    db: Session = Depends(get_db),
):
    """Display pre-filled company edit form."""
    service = CompanyService(db)
    try:
        company = service.get_company(company_id=id, user_id=user.id)
    except CompanyNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Company not found.",
        )

    return templates.TemplateResponse(
        request=request,
        name="companies/form.html",
        context={
            "user": user,
            "company": company,
            "form_data": {
                "name": company.name,
                "website": company.website or "",
                "industry": company.industry or "",
                "location": company.location or "",
                "notes": company.notes or "",
            },
            "error": None,
        },
    )


@router.post(
    "/companies/{id}/edit",
    summary="Submit Company Edit",
    response_description="Updates company and redirects to detail page via PRG",
    dependencies=[Depends(verify_csrf)],
)
async def edit_company(
    request: Request,
    id: uuid.UUID,
    name: str = Form(""),
    website: Optional[str] = Form(None),
    industry: Optional[str] = Form(None),
    location: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
    user: User = Depends(require_authenticated_user),
    db: Session = Depends(get_db),
):
    """Handle company edit form submission with PRG flow.

    Verifies ownership before applying updates. Enforces uniqueness.
    Re-renders form on validation error with status 422.
    """
    service = CompanyService(db)
    try:
        company = service.get_company(company_id=id, user_id=user.id)
    except CompanyNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Company not found.",
        )

    raw_form = {
        "name": name,
        "website": website or "",
        "industry": industry or "",
        "location": location or "",
        "notes": notes or "",
    }

    try:
        update_in = CompanyUpdate(
            name=name,
            website=website,
            industry=industry,
            location=location,
            notes=notes,
        )
    except (ValidationError, ValueError) as exc:
        error_msg = exc.errors()[0]["msg"] if hasattr(exc, "errors") else str(exc)
        if error_msg.startswith("Value error, "):
            error_msg = error_msg[len("Value error, "):]
        return templates.TemplateResponse(
            request=request,
            name="companies/form.html",
            context={
                "user": user,
                "company": company,
                "form_data": raw_form,
                "error": error_msg,
            },
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    try:
        updated_company = service.update_company(
            company_id=id,
            user_id=user.id,
            company_in=update_in,
        )
    except DuplicateCompanyNameError as exc:
        return templates.TemplateResponse(
            request=request,
            name="companies/form.html",
            context={
                "user": user,
                "company": company,
                "form_data": raw_form,
                "error": str(exc),
            },
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    except CompanyNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Company not found.",
        )

    response = RedirectResponse(
        url=f"/companies/{updated_company.id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )
    set_flash_message(
        response,
        f"Company '{updated_company.name}' updated successfully.",
        category="success",
    )
    return response


@router.post(
    "/companies/{id}/archive",
    summary="Archive Company",
    response_description="Archives company and redirects to company directory via PRG",
    dependencies=[Depends(verify_csrf)],
)
async def archive_company(
    id: uuid.UUID,
    user: User = Depends(require_authenticated_user),
    db: Session = Depends(get_db),
):
    """Mark an owned company as archived (soft-deleted).

    Preserves the record in the database. Returns 404 for unowned companies.
    """
    service = CompanyService(db)
    try:
        company = service.archive_company(company_id=id, user_id=user.id)
    except CompanyNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Company not found.",
        )

    response = RedirectResponse(
        url="/companies",
        status_code=status.HTTP_303_SEE_OTHER,
    )
    set_flash_message(
        response,
        f"Company '{company.name}' archived.",
        category="info",
    )
    return response


@router.post(
    "/companies/{id}/unarchive",
    summary="Unarchive Company",
    response_description="Restores company from archive and redirects to detail page via PRG",
    dependencies=[Depends(verify_csrf)],
)
async def unarchive_company(
    id: uuid.UUID,
    user: User = Depends(require_authenticated_user),
    db: Session = Depends(get_db),
):
    """Restore an archived company owned by the user."""
    service = CompanyService(db)
    try:
        company = service.update_company(
            company_id=id,
            user_id=user.id,
            company_in=CompanyUpdate(is_archived=False),
        )
    except CompanyNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Company not found.",
        )

    response = RedirectResponse(
        url=f"/companies/{id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )
    set_flash_message(
        response,
        f"Company '{company.name}' unarchived.",
        category="success",
    )
    return response

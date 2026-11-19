"""Services package for ApplyTrack business logic."""

from app.services.application_service import (
    ApplicationNotFoundError,
    ApplicationService,
    ApplicationServiceError,
    InvalidApplicationCompanyError,
    InvalidApplicationUpdateError,
    InvalidSalaryRangeError,
)
from app.services.auth_service import (
    AuthService,
    AuthServiceError,
    DuplicateEmailError,
    InvalidCredentialsError,
    InvalidSessionError,
)
from app.services.company_service import (
    CompanyNotFoundError,
    CompanyService,
    CompanyServiceError,
    DuplicateCompanyNameError,
    InvalidCompanyUpdateError,
)

__all__ = [
    "AuthService",
    "AuthServiceError",
    "DuplicateEmailError",
    "InvalidCredentialsError",
    "InvalidSessionError",
    "CompanyService",
    "CompanyServiceError",
    "CompanyNotFoundError",
    "DuplicateCompanyNameError",
    "InvalidCompanyUpdateError",
    "ApplicationService",
    "ApplicationServiceError",
    "ApplicationNotFoundError",
    "InvalidApplicationCompanyError",
    "InvalidSalaryRangeError",
    "InvalidApplicationUpdateError",
]

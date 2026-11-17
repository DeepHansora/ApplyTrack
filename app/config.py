"""Application configuration and environment settings."""

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Strongly typed application settings loaded from environment and .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    APP_NAME: str = "ApplyTrack"
    ENVIRONMENT: str = "development"
    DATABASE_URL: str = Field(
        ...,
        description="PostgreSQL connection string using psycopg driver",
    )
    SECRET_KEY: str = Field(
        ...,
        min_length=32,
        description="Cryptographic secret key for session and CSRF protection",
    )

    # Phase 2 Session & Cookie Settings
    SESSION_IDLE_TIMEOUT_MINUTES: int = 120
    SESSION_ABSOLUTE_LIFETIME_HOURS: int = 24
    SESSION_COOKIE_NAME: str = "applytrack_session"
    SESSION_COOKIE_HTTPONLY: bool = True
    SESSION_COOKIE_SAMESITE: str = "lax"
    SESSION_COOKIE_PATH: str = "/"
    SESSION_COOKIE_SECURE: bool = False

    # Phase 2 CSRF Cookie Settings
    CSRF_COOKIE_NAME: str = "applytrack_csrf"
    CSRF_COOKIE_HTTPONLY: bool = True
    CSRF_COOKIE_SAMESITE: str = "lax"
    CSRF_COOKIE_PATH: str = "/"
    CSRF_COOKIE_SECURE: bool = False
    CSRF_COOKIE_MAX_AGE: int = 86400  # 24 hours

    # Phase 2 Flash Message Cookie Settings
    FLASH_COOKIE_NAME: str = "applytrack_flash"
    FLASH_COOKIE_HTTPONLY: bool = True
    FLASH_COOKIE_SAMESITE: str = "lax"
    FLASH_COOKIE_PATH: str = "/"
    FLASH_COOKIE_SECURE: bool = False
    FLASH_COOKIE_MAX_AGE: int = 300  # 5 minutes

    @model_validator(mode="after")
    def compute_cookie_secure(self) -> "Settings":
        """Default cookie secure flags to True in production and False in development."""
        is_prod = self.ENVIRONMENT.lower() == "production"
        if "SESSION_COOKIE_SECURE" not in self.model_fields_set:
            self.SESSION_COOKIE_SECURE = is_prod
        if "CSRF_COOKIE_SECURE" not in self.model_fields_set:
            self.CSRF_COOKIE_SECURE = is_prod
        if "FLASH_COOKIE_SECURE" not in self.model_fields_set:
            self.FLASH_COOKIE_SECURE = is_prod
        return self


settings = Settings()

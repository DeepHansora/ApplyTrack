"""FastAPI dependencies for request handling and resource management."""

from typing import Generator
from sqlalchemy.orm import Session

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

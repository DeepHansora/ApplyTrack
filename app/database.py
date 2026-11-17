"""SQLAlchemy database engine and session configuration."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings

# Create SQLAlchemy 2.x Engine
# pool_pre_ping=True: checks connection health with a lightweight ping prior to checkout
# pool_size=5, max_overflow=10: lightweight pooling ideal for local development
engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
    echo=False,
)

# Session factory for generating local database sessions
SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)

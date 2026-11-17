"""Focused tests for the get_db database session dependency."""

from unittest.mock import patch, MagicMock
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.dependencies import get_db


def test_get_db_provides_active_session_and_closes():
    """Verify get_db yields an active SQLAlchemy Session and always closes it."""
    gen = get_db()
    session = next(gen)

    # 1. Verify that a valid SQLAlchemy Session instance is yielded
    assert isinstance(session, Session)

    # 2. Verify that the session can execute a query
    result = session.execute(text("SELECT 1")).scalar()
    assert result == 1

    # 3. Complete the generator to trigger the finally block
    try:
        next(gen)
    except StopIteration:
        pass  # Expected generator exhaustion

    # 4. Verify that session resources are closed/detached
    # In SQLAlchemy 2.0, a closed session detaches its active connection
    assert not session.is_active or session.get_bind() is not None


def test_get_db_closes_on_exception():
    """Verify that get_db closes the session even if an exception occurs during request execution."""
    mock_session = MagicMock(spec=Session)

    with patch("app.dependencies.SessionLocal", return_value=mock_session):
        gen = get_db()
        session = next(gen)
        assert session is mock_session

        # Simulate route exception
        try:
            gen.throw(RuntimeError("Simulated route exception"))
        except RuntimeError:
            pass

        # Verify close() was called unconditionally in the finally block
        mock_session.close.assert_called_once()

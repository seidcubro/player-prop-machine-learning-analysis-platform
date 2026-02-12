"""Database session and connectivity helpers for the API service.

This module centralizes SQLAlchemy engine/session construction and provides the
FastAPI dependency (`get_db`) used by route handlers.

Design goals:
- single source of truth for DATABASE_URL parsing
- short-lived, request-scoped DB sessions
- safe teardown/rollback on errors
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import os

DATABASE_URL = os.getenv("DATABASE_URL")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)

def get_db():
    """FastAPI dependency that yields a request-scoped SQLAlchemy session.

    Route handlers declare `db: Session = Depends(get_db)` to receive a session
    bound to the API service engine.

    Yields:
        sqlalchemy.orm.Session: An open SQLAlchemy session for the duration of the request.

    Notes:
        This helper intentionally keeps session lifecycle simple:
        - A new session is created per request.
        - The session is always closed in `finally`.

        Transaction boundaries are controlled by the handler. If a handler writes
        and then raises, call `db.rollback()` before re-raising so the connection
        returns to the pool in a clean state.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

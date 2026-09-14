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

# Pooling set explicitly, because the defaults assume a process that dies often.
#
# pool_pre_ping is the one that matters. Without it SQLAlchemy hands out
# whatever connection is in the pool and finds out it is dead when the query
# fails, so the first request after a Postgres restart, a container update or an
# idle timeout returns an error rather than reconnecting. On a laptop the
# database never goes away and this never shows up; on a single small server it
# restarts for updates and the site answers 500s until the pool turns over.
#
# pool_recycle is the same problem from the other side: a connection idle for
# hours can be dropped by the server or something in between without either end
# noticing, and overnight this API is idle for hours at a time.
#
# The sizes are deliberate rather than inherited. Ten plus five is far more than
# a read-mostly site behind a 60-per-minute rate limit needs, and small enough
# that the API cannot exhaust Postgres's connection slots and lock out the
# pipeline that has to write to it.
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_recycle=1800,
    pool_size=10,
    max_overflow=5,
)
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

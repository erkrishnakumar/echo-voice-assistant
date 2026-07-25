"""
Database plumbing: the SQLAlchemy engine, a session factory, and the
declarative Base that models inherit from.

Import `SessionLocal` to get a session, `Base` to define models, and call
`init_db()` once at startup to create tables.
"""

from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from echo.config import settings

# pool_pre_ping avoids "server closed the connection" errors after the DB
# has been idle — it checks the connection is alive before handing it out.
engine = create_engine(settings.database_url, pool_pre_ping=True, future=True)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def init_db() -> None:
    """Create all tables. Safe to call repeatedly — only creates what's missing."""
    # import models so they're registered on Base before create_all
    from echo import models  # noqa: F401

    Base.metadata.create_all(bind=engine)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope: commit on success, rollback on error, always close."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
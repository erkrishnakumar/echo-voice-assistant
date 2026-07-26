"""
Shared pytest setup. Points the app at an in-memory SQLite database so tests
run fast and isolated, without needing Postgres or Docker.
"""

import os
import sys
from pathlib import Path

# make src/ importable
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# use a throwaway SQLite DB for tests (set BEFORE importing echo.config)
os.environ["DATABASE_URL"] = "sqlite:///./test_echo.db"

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db():
    """Create tables fresh for each test, drop them after."""
    from echo.db import Base, engine, init_db
    init_db()
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(scope="session", autouse=True)
def cleanup_db_file():
    yield
    # dispose the engine so Windows releases the SQLite file handle before delete
    try:
        from echo.db import engine
        engine.dispose()
    except Exception:
        pass
    db = Path("test_echo.db")
    if db.exists():
        try:
            db.unlink()
        except PermissionError:
            pass  # Windows may still hold it briefly; harmless leftover
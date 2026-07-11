"""Shared pytest fixtures for TRACE backend tests.

Tests run against an in-memory SQLite engine, separate from the app's
production Postgres engine. The same SQLAlchemy 2.0 models work on both
backends; SQLite is only the test driver here.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base

# Importing models registers all tables on Base.metadata before create_all.
import app.models  # noqa: F401


@pytest.fixture()
def db_session():
    """Yield a Session bound to a fresh in-memory SQLite DB; clean up after."""
    engine = create_engine(
        "sqlite:///:memory:",
        # JSON columns render fine on SQLite; native JSON is supported.
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()

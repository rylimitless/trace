"""Shared pytest fixtures for TRACE backend tests.

Tests run against an in-memory SQLite engine, separate from the app's
production Postgres engine. The same SQLAlchemy 2.0 models work on both
backends; SQLite is only the test driver here.

Two DB-backed fixtures live here:

* ``db_session`` — a bare SQLAlchemy Session (used by model/state-machine/event
  tests in Tasks 2–4). No app, no client.
* ``client`` — a ``TestClient(app)`` with ``get_db`` overridden to a fresh
  in-memory engine, so HTTP requests and any direct ``db_session`` access in
  the same test share one DB. Use this for route/auth tests.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db

# Importing models registers all tables on Base.metadata before create_all.
import app.models  # noqa: F401


def _fresh_engine():
    """A new in-memory SQLite engine with the schema created.

    Used by the bare ``db_session`` fixture, which holds one session (one
    connection) open for the whole test — so plain in-memory SQLite is fine.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        # JSON columns render fine on SQLite; native JSON is supported.
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    return engine


def _fresh_shared_engine():
    """An in-memory SQLite engine that shares ONE connection across sessions.

    Plain ``sqlite:///:memory:`` gives every new connection a brand-new empty
    database, which is fine when a test holds one session open (``db_session``)
    but breaks HTTP tests: the TestClient's request path opens its own
    connections, and those would see no tables. ``StaticPool`` pins every
    session to the same underlying connection so the schema (and data) created
    on one session is visible to all others.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture()
def db_session():
    """Yield a Session bound to a fresh in-memory SQLite DB; clean up after."""
    engine = _fresh_engine()
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture()
def client():
    """A TestClient(app) backed by a fresh in-memory DB via get_db override.

    The same SessionLocal is used for every ``get_db`` call the app makes
    during the test, so data seeded through the client is visible to later
    requests. Override is cleared on teardown so other tests are unaffected.
    """
    # Local import keeps the fixture self-contained and avoids importing the
    # app (and thus mounting routers) when only db_session is requested.
    from app.main import app

    engine = _fresh_shared_engine()
    TestSessionLocal = sessionmaker(
        bind=engine, autocommit=False, autoflush=False
    )

    def _override_get_db():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as test_client:
        # Expose the seeding session factory on the client so tests can insert
        # rows that the app's overridden get_db will then see. Attached as an
        # attribute rather than returning a tuple, so ``client`` still behaves
        # like a normal TestClient everywhere else.
        test_client.db_session = TestSessionLocal  # type: ignore[attr-defined]
        yield test_client
    # Drop the attribute so attribute state can't leak between tests if the
    # TestClient instance were ever reused (it isn't, but be explicit).
    try:
        del test_client.db_session  # type: ignore[attr-defined]
    except AttributeError:
        pass
    app.dependency_overrides.pop(get_db, None)
    Base.metadata.drop_all(engine)
    engine.dispose()

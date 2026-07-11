"""Tests for Task 5: session-cookie login, role dependencies, capture token.

Four concerns:

1. **bcrypt round-trip** — ``hash_password`` then ``verify_password`` agrees on
   the right password and rejects a wrong one.
2. **Login flow over HTTP** — correct password sets the session cookie and
   returns the role; wrong password returns 401 and sets no cookie. Logout
   clears the cookie.
3. **Role dependencies** — a protected route using ``Depends(require_admin)``
   allows an admin, 403s a buyer, and 401s an unauthenticated request.
4. **Capture token** — ``generate_capture_token`` writes a unique, non-empty
   token and a ~24h expiry, and is committed (visible on a fresh session).

The HTTP tests use the ``client`` fixture (TestClient + overridden get_db on a
fresh in-memory engine). The TestClient stores and resends cookies across
requests on the same instance, so a login on ``client`` is seen by the
protected route on the same ``client``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import Depends

from app.auth import (
    generate_capture_token,
    hash_password,
    require_admin,
    verify_password,
)
from app.models import Batch, Farmer, User, UserRole


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_user(db, *, email: str, password: str, role: UserRole) -> User:
    """Insert and commit a User with a bcrypt-hashed password; return it."""
    user = User(
        email=email,
        password_hash=hash_password(password),
        role=role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


# ---------------------------------------------------------------------------
# bcrypt hash/verify
# ---------------------------------------------------------------------------


def test_hash_and_verify_password_roundtrip():
    """verify_password accepts the original and rejects a different string."""
    hashed = hash_password("s3cret!")
    assert hashed != "s3cret!"
    assert verify_password("s3cret!", hashed) is True
    assert verify_password("wrong", hashed) is False


def test_hashed_passwords_are_salt_unique():
    """Two hashes of the same password differ (random salt)."""
    a = hash_password("same")
    b = hash_password("same")
    assert a != b
    assert verify_password("same", a) and verify_password("same", b)


# ---------------------------------------------------------------------------
# Login / logout over HTTP
# ---------------------------------------------------------------------------


def test_login_correct_password_sets_cookie_and_returns_role(client):
    """200 + ``{role}`` + a Set-Cookie carrying a signed session value."""
    db = client.db_session()
    _seed_user(
        db,
        email="admin@trace.local",
        password="correct-horse",
        role=UserRole.admin,
    )

    resp = client.post(
        "/auth/login",
        json={"email": "admin@trace.local", "password": "correct-horse"},
    )

    assert resp.status_code == 200
    assert resp.json() == {"role": "admin"}
    # The session cookie is present in the response.
    assert "trace_session" in resp.cookies
    assert resp.cookies["trace_session"]  # non-empty signed value


def test_login_wrong_password_returns_401_and_sets_no_cookie(client):
    """401 on a bad password; no session cookie in the response."""
    db = client.db_session()
    _seed_user(
        db,
        email="admin@trace.local",
        password="correct-horse",
        role=UserRole.admin,
    )

    resp = client.post(
        "/auth/login",
        json={"email": "admin@trace.local", "password": "battery-staple"},
    )

    assert resp.status_code == 401
    # No session cookie is issued on failure.
    assert "trace_session" not in resp.cookies


def test_login_unknown_email_returns_401(client):
    """An email with no user row also 401s (same error as wrong password)."""
    resp = client.post(
        "/auth/login",
        json={"email": "nobody@trace.local", "password": "whatever"},
    )
    assert resp.status_code == 401


def test_logout_clears_session_cookie(client):
    """After logout, the response instructs the client to delete the cookie."""
    db = client.db_session()
    _seed_user(
        db,
        email="admin@trace.local",
        password="pw",
        role=UserRole.admin,
    )
    client.post(
        "/auth/login", json={"email": "admin@trace.local", "password": "pw"}
    )
    # Cookie is now held by the client instance.
    assert "trace_session" in client.cookies

    resp = client.post("/auth/logout")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    # The Set-Cookie on logout deletes the cookie (Max-Age=0 / empty value).
    set_cookie = resp.headers.get("set-cookie", "")
    assert "trace_session" in set_cookie
    # Either the cookie is expired or its value is blanked.
    assert "Max-Age=0" in set_cookie or 'trace_session=""' in set_cookie


# ---------------------------------------------------------------------------
# Role dependencies on a protected route
# ---------------------------------------------------------------------------


def _make_protected_client(client):
    """Attach a throwaway admin-only route to the running app for the test.

    Mounting ad-hoc avoids polluting main.py with a test-only endpoint while
    still exercising the real ``require_admin`` dependency over HTTP.
    """
    from app.main import app

    @app.get("/__test_admin_only")
    def _admin_only(_=Depends(require_admin)):
        return {"ok": True}

    return client


def test_protected_route_allows_admin(client):
    """An admin session reaches the protected handler and gets 200."""
    db = client.db_session()
    _seed_user(
        db,
        email="admin@trace.local",
        password="pw",
        role=UserRole.admin,
    )
    _make_protected_client(client)
    client.post(
        "/auth/login", json={"email": "admin@trace.local", "password": "pw"}
    )

    resp = client.get("/__test_admin_only")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_protected_route_forbidden_for_buyer(client):
    """A logged-in buyer hits the admin-only route and gets 403 (not 401)."""
    db = client.db_session()
    _seed_user(
        db,
        email="buyer@trace.local",
        password="pw",
        role=UserRole.premium_buyer,
    )
    _make_protected_client(client)
    client.post(
        "/auth/login", json={"email": "buyer@trace.local", "password": "pw"}
    )

    resp = client.get("/__test_admin_only")
    assert resp.status_code == 403


def test_protected_route_unauthorized_without_cookie(client):
    """No session cookie at all -> 401 on the protected route."""
    _make_protected_client(client)
    resp = client.get("/__test_admin_only")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Capture token
# ---------------------------------------------------------------------------


def test_generate_capture_token_sets_unique_token_and_expiry(db_session):
    """generate_capture_token writes a non-empty token + ~24h expiry, committed."""
    farmer = Farmer(name="Siti", lat=1.30, lng=103.85)
    db_session.add(farmer)
    db_session.commit()
    batch = Batch(
        farmer_id=farmer.id,
        crop="tomato",
        kg=20.0,
        status="HARVESTED",
        capture_token="initial-placeholder",
    )
    db_session.add(batch)
    db_session.commit()

    token = generate_capture_token(db_session, batch)

    now = datetime.now(timezone.utc)
    assert isinstance(token, str)
    assert token  # non-empty
    assert token != "initial-placeholder"  # replaced
    # Expiry is ~24h ahead (allow a generous window for test latency).
    assert batch.capture_token_expires_at is not None
    expiry = batch.capture_token_expires_at
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    lower = now + timedelta(hours=23, minutes=55)
    upper = now + timedelta(hours=24, minutes=5)
    assert lower <= expiry <= upper


def test_generate_capture_token_is_unique_across_batches(db_session):
    """Two tokens generated for two batches are distinct (urlsafe randomness)."""
    farmer = Farmer(name="Ali", lat=1.31, lng=103.84)
    db_session.add(farmer)
    db_session.commit()

    tokens = []
    for _ in range(2):
        b = Batch(
            farmer_id=farmer.id,
            crop="tomato",
            kg=10.0,
            status="HARVESTED",
            capture_token="placeholder",
        )
        db_session.add(b)
        db_session.commit()
        tokens.append(generate_capture_token(db_session, b))

    assert tokens[0] != tokens[1]
    assert all(t and len(t) >= 16 for t in tokens)


def test_generate_capture_token_persists_on_fresh_session(db_session):
    """The token survives a commit and is visible from a new session."""
    farmer = Farmer(name="Min", lat=1.32, lng=103.83)
    db_session.add(farmer)
    db_session.commit()
    batch = Batch(
        farmer_id=farmer.id,
        crop="chili",
        kg=5.0,
        status="HARVESTED",
        capture_token="placeholder",
    )
    db_session.add(batch)
    db_session.commit()

    token = generate_capture_token(db_session, batch)

    # Re-read from the DB to confirm it was committed (not just in-memory).
    db_session.expire_all()
    db_session.refresh(batch)
    assert batch.capture_token == token
    assert batch.capture_token_expires_at is not None

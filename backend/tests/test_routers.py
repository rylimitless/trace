"""Tests for Task 7: the REST surface (501 stubs + real SSE + scoped list).

Covers three things the Definition of Done and rubric care about:

1. **Every gated route enforces its role** — no cookie → 401, wrong role → 403,
   correct role → the stub's 501 (or the real 200 for the two implemented
   routes). This is the contract the later slices rely on.
2. **``GET /contracts/mine`` is real** — returns only the calling premium
   buyer's contracts (``buyer_id`` scoping) and never leaks another buyer's.
3. **``GET /admin/stream`` is a real SSE endpoint** — admin-gated, returns a
   ``StreamingResponse`` with ``media_type=text/event-stream`` wired to the
   live event bus, and yields a published event as an SSE frame.

SSE + TestClient interaction
----------------------------
The SSE generator (``app.events.subscribe``) loops forever, so a blocking
``TestClient.get('/admin/stream')`` would hang waiting for the body to finish.
We therefore split the SSE verification into two parts that together prove the
real wiring without blocking:

* **Auth gating over HTTP** (401/403) — these complete normally because auth
  fails before the stream is established, so the generator is never consumed.
* **Stream wiring** — assert the handler returns a ``StreamingResponse`` with
  the right ``media_type``, then drive its ``body_iterator`` directly, publish
  one event on the bus, and read back the formatted SSE frame. This exercises
  the exact generator the route hands to ``StreamingResponse``.

Cookie isolation
----------------
A ``TestClient`` holds one cookie jar, so a single instance can only be logged
in as one identity at a time. Each test therefore logs in as exactly one user
and asserts that user's outcomes; cross-role 403 cases are separate tests
(each gets its own fresh ``client``).
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.responses import StreamingResponse

from app.auth import hash_password
from app.events import publish, _subscribers
from app.models import Buyer, BuyerType, Contract, ContractStatus, User, UserRole
from app.routers.admin import _event_generator, admin_stream

# ---------------------------------------------------------------------------
# Seeding + login helpers
# ---------------------------------------------------------------------------


def _seed_user(db, *, email: str, password: str, role: UserRole, buyer=None) -> User:
    """Insert + commit a User (optionally linked to a Buyer row); return it."""
    user = User(
        email=email,
        password_hash=hash_password(password),
        role=role,
        buyer_id=buyer.id if buyer is not None else None,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _seed_buyer(db, *, name: str, type_: BuyerType) -> Buyer:
    b = Buyer(name=name, type=type_)
    db.add(b)
    db.commit()
    db.refresh(b)
    return b


def _login(client, email: str, password: str = "pw") -> None:
    """Log in via the auth router so the TestClient holds the session cookie."""
    resp = client.post("/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text


# Fixed demo credentials used across the per-role tests.
ADMIN_EMAIL = "admin@trace.local"
PREMIUM_EMAIL = "premium@trace.local"
SECONDARY_EMAIL = "secondary@trace.local"
COMPOSTER_EMAIL = "composter@trace.local"
DEMO_PW = "pw"


def _seed_all_roles(client) -> dict:
    """Seed one user per role (buyers linked to Buyer rows); return emails.

    Used by tests that need to flip between identities on fresh clients — each
    test picks the identity it wants to log in as.
    """
    db = client.db_session()
    _seed_user(db, email=ADMIN_EMAIL, password=DEMO_PW, role=UserRole.admin)
    premium_buyer = _seed_buyer(db, name="Resort", type_=BuyerType.premium)
    _seed_user(
        db,
        email=PREMIUM_EMAIL,
        password=DEMO_PW,
        role=UserRole.premium_buyer,
        buyer=premium_buyer,
    )
    secondary_buyer = _seed_buyer(db, name="School", type_=BuyerType.secondary)
    _seed_user(
        db,
        email=SECONDARY_EMAIL,
        password=DEMO_PW,
        role=UserRole.secondary_buyer,
        buyer=secondary_buyer,
    )
    composter_buyer = _seed_buyer(db, name="Composter A", type_=BuyerType.composter)
    _seed_user(
        db,
        email=COMPOSTER_EMAIL,
        password=DEMO_PW,
        role=UserRole.composter,
        buyer=composter_buyer,
    )
    db.close()
    return {
        UserRole.admin: ADMIN_EMAIL,
        UserRole.premium_buyer: PREMIUM_EMAIL,
        UserRole.secondary_buyer: SECONDARY_EMAIL,
        UserRole.composter: COMPOSTER_EMAIL,
    }


# ---------------------------------------------------------------------------
# 1. Unauthenticated access → 401 on every gated route
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/batches"),
        ("get", "/contracts"),
        ("get", "/contracts/mine"),
        ("post", "/contracts/1/confirm"),
        ("post", "/batches/1/dispute"),
        ("get", "/payouts"),
        ("get", "/demand"),
        ("get", "/offers"),
        ("get", "/pickups"),
        ("get", "/admin/stream"),
    ],
)
def test_gated_route_unauthenticated_is_401(client, method, path):
    """No session cookie → 401 on every role-gated route."""
    resp = getattr(client, method)(path)
    assert resp.status_code == 401


def test_capture_is_unauthenticated_and_returns_501(client):
    """``POST /capture/{token}`` has no session gate — the token is the auth.

    The request reaches the handler and gets the stub's 501 (not 401), proving
    the route is intentionally open at the transport layer.
    """
    resp = client.post("/capture/any-token")
    assert resp.status_code == 501


# ---------------------------------------------------------------------------
# 2. Admin-gated routes: 403 for non-admin, 501 for admin
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    ["/contracts"],
)
class TestAdminRoutes:
    """Admin-only list views: wrong role 403s, admin reaches the 501 stub.

    NOTE: ``/demand``, ``/batches`` and ``/payouts`` were promoted to real
    handlers in Slice D (return 200, not 501) and are now covered by
    tests/test_admin_router.py and tests/test_read_endpoints.py.
    """

    def test_wrong_role_buyer_is_403(self, client, path):
        emails = _seed_all_roles(client)
        _login(client, emails[UserRole.premium_buyer])
        assert client.get(path).status_code == 403

    def test_wrong_role_composter_is_403(self, client, path):
        emails = _seed_all_roles(client)
        _login(client, emails[UserRole.composter])
        assert client.get(path).status_code == 403

    def test_admin_reaches_501_stub(self, client, path):
        emails = _seed_all_roles(client)
        _login(client, emails[UserRole.admin])
        assert client.get(path).status_code == 501


# ---------------------------------------------------------------------------
# 3. Buyer/composter-gated routes
# ---------------------------------------------------------------------------


def test_offers_allows_secondary_buyer_only(client):
    """``GET /offers`` → secondary buyer 200, premium buyer 403, composter 403.

    The handler is real as of Slice D (covered fully by
    tests/test_read_endpoints.py); here we only assert the role gate and that
    the secondary buyer reaches a real 200 (empty list with no batches).
    """
    emails = _seed_all_roles(client)

    _login(client, emails[UserRole.secondary_buyer])
    assert client.get("/offers").status_code == 200

    _login_as_fresh(client, emails[UserRole.premium_buyer])
    assert client.get("/offers").status_code == 403


def test_pickups_allows_composter_only(client):
    """``GET /pickups`` → composter 200, others 403.

    The handler is real as of Slice D (covered fully by
    tests/test_read_endpoints.py); here we only assert the role gate and that
    the composter reaches a real 200 (empty list with no batches).
    """
    emails = _seed_all_roles(client)

    _login(client, emails[UserRole.composter])
    assert client.get("/pickups").status_code == 200

    _login_as_fresh(client, emails[UserRole.secondary_buyer])
    assert client.get("/pickups").status_code == 403


def test_contracts_confirm_allows_premium_buyer_only(client):
    """``POST /contracts/{id}/confirm`` → premium buyer 404 (no such contract),
    secondary 403.

    The handler is real as of Slice D (covered fully by
    tests/test_contracts_router.py). ``_seed_all_roles`` seeds no contracts,
    so a confirm on id=1 hits the 404 branch; the role gate still rejects a
    secondary buyer before any DB lookup.
    """
    emails = _seed_all_roles(client)

    _login(client, emails[UserRole.premium_buyer])
    assert client.post("/contracts/1/confirm").status_code == 404

    _login_as_fresh(client, emails[UserRole.secondary_buyer])
    assert client.post("/contracts/1/confirm").status_code == 403


def test_batches_dispute_allows_premium_buyer_only(client):
    """``POST /batches/{id}/dispute`` → premium buyer 404 (no such batch),
    secondary 403.

    The handler is real as of Slice D (covered fully by
    tests/test_read_endpoints.py). ``_seed_all_roles`` seeds no batches, so a
    dispute on id=1 hits the 404 branch; the role gate still rejects a
    secondary buyer before any DB lookup.
    """
    emails = _seed_all_roles(client)

    _login(client, emails[UserRole.premium_buyer])
    assert client.post("/batches/1/dispute").status_code == 404

    _login_as_fresh(client, emails[UserRole.secondary_buyer])
    assert client.post("/batches/1/dispute").status_code == 403


def _login_as_fresh(client, email: str) -> None:
    """Re-login on the same client, clearing the prior cookie first.

    The TestClient cookie jar is mutable, so logging out then in switches
    identity in place — used by tests that need to assert two roles on one
    route without paying for a second fixture.
    """
    client.post("/auth/logout")
    _login(client, email)


# ---------------------------------------------------------------------------
# 4. /contracts/mine — real, scoped to buyer_id
# ---------------------------------------------------------------------------


def _seed_premium_with_contracts(client):
    """Seed a premium buyer with one own contract + another buyer's contract.

    The other buyer's contract must NOT appear in ``/contracts/mine``.
    Returns the premium buyer's email.
    """
    db = client.db_session()
    own = _seed_buyer(db, name="Resort", type_=BuyerType.premium)
    other = _seed_buyer(db, name="Other Resort", type_=BuyerType.premium)
    _seed_user(
        db,
        email=PREMIUM_EMAIL,
        password=DEMO_PW,
        role=UserRole.premium_buyer,
        buyer=own,
    )
    db.add(
        Contract(
            buyer_id=own.id,
            crop="tomato",
            grade="A",
            kg_target=200.0,
            price_per_kg=4.0,
            status=ContractStatus.open,
        )
    )
    # A second buyer's contract — must not leak into the premium buyer's list.
    db.add(
        Contract(
            buyer_id=other.id,
            crop="chili",
            grade="B",
            kg_target=50.0,
            price_per_kg=2.0,
            status=ContractStatus.open,
        )
    )
    db.commit()
    db.close()
    return PREMIUM_EMAIL


def test_contracts_mine_returns_only_own_contracts(client):
    """Premium buyer sees only contracts with their ``buyer_id``."""
    email = _seed_premium_with_contracts(client)
    _login(client, email)

    resp = client.get("/contracts/mine")
    assert resp.status_code == 200
    rows = resp.json()
    assert isinstance(rows, list)
    assert len(rows) == 1
    only = rows[0]
    assert set(only.keys()) == {"id", "crop", "grade", "kg_target", "status"}
    assert only["crop"] == "tomato"
    assert only["grade"] == "A"
    assert only["kg_target"] == 200.0
    assert only["status"] == "open"
    # The other buyer's contract did not leak.
    assert all(r["crop"] != "chili" for r in rows)


def test_contracts_mine_empty_when_buyer_has_no_contracts(client):
    """A premium buyer with zero contracts gets 200 + empty list (not 404)."""
    db = client.db_session()
    b = _seed_buyer(db, name="Empty Resort", type_=BuyerType.premium)
    _seed_user(
        db,
        email="empty@trace.local",
        password=DEMO_PW,
        role=UserRole.premium_buyer,
        buyer=b,
    )
    db.close()
    _login(client, "empty@trace.local")

    resp = client.get("/contracts/mine")
    assert resp.status_code == 200
    assert resp.json() == []


def test_contracts_mine_forbidden_for_secondary_buyer(client):
    """``GET /contracts/mine`` is premium-only → secondary buyer gets 403."""
    emails = _seed_all_roles(client)
    _login(client, emails[UserRole.secondary_buyer])
    assert client.get("/contracts/mine").status_code == 403


def test_contracts_mine_forbidden_for_admin(client):
    """Even an admin cannot use the buyer-scoped ``/mine`` endpoint → 403.

    ``/mine`` is intentionally premium-buyer-scoped; admin has the separate
    ``GET /contracts`` list. This documents the boundary.
    """
    emails = _seed_all_roles(client)
    _login(client, emails[UserRole.admin])
    assert client.get("/contracts/mine").status_code == 403


# ---------------------------------------------------------------------------
# 5. /admin/stream — real SSE endpoint
# ---------------------------------------------------------------------------


def test_admin_stream_unauthenticated_is_401(client):
    """No cookie → 401 before the stream is established (no hang)."""
    resp = client.get("/admin/stream")
    assert resp.status_code == 401


def test_admin_stream_wrong_role_is_403(client):
    """Non-admin → 403 before the stream is established (no hang)."""
    emails = _seed_all_roles(client)
    _login(client, emails[UserRole.premium_buyer])
    assert client.get("/admin/stream").status_code == 403


def test_admin_stream_returns_sse_response_with_correct_media_type():
    """The handler returns a ``StreamingResponse`` with ``text/event-stream``.

    Calling the handler directly (with an admin User) lets us inspect the
    response object's ``media_type`` without consuming the infinite stream,
    which would hang a blocking TestClient. This is the exact object FastAPI
    sends over the wire.
    """
    admin = User(email=ADMIN_EMAIL, password_hash="x", role=UserRole.admin)
    resp = admin_stream(admin)
    assert isinstance(resp, StreamingResponse)
    assert resp.media_type == "text/event-stream"


def test_admin_stream_yields_published_event_as_sse_frame():
    """The route's generator emits a real bus event as an SSE-formatted frame.

    Drives ``_event_generator`` (the async generator the route passes to
    ``StreamingResponse``) directly: schedules a ``publish`` on the running
    loop, reads one frame, then closes. Proves the stream is wired to the live
    event bus and emits ``data: <json>\\n\\n``.
    """
    # Ensure no stale subscribers leak from another test.
    _subscribers.clear()

    async def main():
        gen = _event_generator()
        loop = asyncio.get_event_loop()
        loop.call_later(0.05, lambda: publish("audit", {"batch_id": 42}))
        try:
            frame = await asyncio.wait_for(gen.__anext__(), timeout=2.0)
        finally:
            await gen.aclose()
        return frame

    frame = asyncio.run(main())
    assert frame.startswith("data: ")
    assert frame.endswith("\n\n")
    assert '"batch_id": 42' in frame


def test_admin_stream_generator_closes_without_leaving_subscribers():
    """Closing the route's generator removes its queue from the bus.

    The route hands ``_event_generator`` (wrapping ``subscribe``) to
    ``StreamingResponse``. When the client disconnects, Starlette closes the
    generator; ``subscribe``'s ``finally`` must then discard its queue so
    reconnects don't leak. The per-subscriber cleanup itself is unit-tested in
    ``tests/test_events.py``; here we confirm the route's generator propagates
    a close to ``subscribe`` and ends up with no leftover queue of its own.
    """
    _subscribers.clear()

    async def run_close_check():
        gen = _event_generator()
        loop = asyncio.get_event_loop()
        loop.call_later(0.02, lambda: publish("ping", {"v": 1}))
        # Consume the one published frame so the generator is mid-flight, then
        # close it (simulating a client disconnect).
        await gen.__anext__()
        await gen.aclose()

    asyncio.run(run_close_check())
    # After the generator is closed, the subscriber set must be empty: the
    # queue this generator registered has been discarded on close.
    assert _subscribers == set()

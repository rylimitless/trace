"""Tests for app/routers/admin.py — GET /demand and the route-disruption toggle.

Uses the ``client`` fixture (TestClient with get_db overridden to in-memory
SQLite). Identities come from app.seed.run_seed (admin / school=secondary_buyer,
password demo1234). Seed creates no Route rows, so the route-disruption test
inserts a minimal Route into client.db_session before flipping the toggle.
"""

import pytest

from app.models import Route
from app.seed import run_seed


def _login(client, email: str, password: str = "demo1234") -> None:
    """Log in via /auth/login so the TestClient holds the session cookie."""
    resp = client.post("/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# GET /demand
# ---------------------------------------------------------------------------


def test_demand_as_admin_returns_anonymized_feed(client):
    """Admin sees the demand feed: a list of {crop,grade,qty_band,urgency}."""
    db = client.db_session()
    run_seed(db)
    db.close()

    _login(client, "admin@trace.demo")
    resp = client.get("/demand")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    expected_keys = {"crop", "grade", "qty_band", "urgency"}
    for item in body:
        assert set(item.keys()) == expected_keys


def test_demand_as_secondary_buyer_forbidden(client):
    """A secondary buyer is not admin -> 403."""
    db = client.db_session()
    run_seed(db)
    db.close()

    _login(client, "school@trace.demo")
    resp = client.get("/demand")
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# POST /admin/demo/route-disruption
# ---------------------------------------------------------------------------


def test_route_disruption_toggle_flips_first_route(client):
    """Admin toggle sets the first Route's washed_out=True in the DB."""
    db = client.db_session()
    run_seed(db)
    # run_seed creates no Route — insert a minimal one tied to a buyer.
    from app.models import Buyer, BuyerType

    buyer = Buyer(name="Route Owner", type=BuyerType.premium)
    db.add(buyer)
    db.commit()
    db.refresh(buyer)
    route = Route(buyer_id=buyer.id, washed_out=False)
    db.add(route)
    db.commit()
    db.refresh(route)
    route_id = route.id
    db.close()

    _login(client, "admin@trace.demo")
    resp = client.post("/admin/demo/route-disruption")
    assert resp.status_code == 200
    body = resp.json()
    assert body["route_id"] == route_id
    assert body["washed_out"] is True
    assert "fallback composter" in body["detail"]

    # Verify the DB row was actually mutated.
    db = client.db_session()
    refreshed = db.get(Route, route_id)
    db.close()
    assert refreshed.washed_out is True


def test_route_disruption_toggle_no_route_returns_404(client):
    """With no Route rows at all, the toggle 404s."""
    db = client.db_session()
    run_seed(db)
    db.close()

    _login(client, "admin@trace.demo")
    resp = client.post("/admin/demo/route-disruption")
    assert resp.status_code == 404

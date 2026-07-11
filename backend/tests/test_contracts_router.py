"""Tests for POST /contracts/{id}/confirm (spec §4b) — Slice D.

The premium buyer who owns a contract confirms fulfillment: its GRADED_FARM
batches are pooled into a VirtualShipment, transitioned to SHIPPED, and a
returning-leg Route is created. A non-owning premium buyer and an admin are
both forbidden (403).
"""

from __future__ import annotations

from app.auth import hash_password
from app.models import (
    Batch,
    Buyer,
    BuyerType,
    Contract,
    ContractStatus,
    Farmer,
    User,
    UserRole,
)
from app.statemachine import State

DEMO_PW = "pw"


# ---------------------------------------------------------------------------
# Seeding helpers
# ---------------------------------------------------------------------------


def _login(client, email: str, password: str = DEMO_PW) -> None:
    """Log in via the auth router so the TestClient holds the session cookie."""
    resp = client.post("/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text


def _seed_buyer(db, *, name: str, type_: BuyerType) -> Buyer:
    b = Buyer(name=name, type=type_)
    db.add(b)
    db.commit()
    db.refresh(b)
    return b


def _seed_premium_user(db, *, email: str, buyer: Buyer) -> User:
    u = User(
        email=email,
        password_hash=hash_password(DEMO_PW),
        role=UserRole.premium_buyer,
        buyer_id=buyer.id,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _seed_contract(db, *, buyer: Buyer, crop="tomato", grade="A") -> Contract:
    c = Contract(
        buyer_id=buyer.id,
        crop=crop,
        grade=grade,
        kg_target=200.0,
        price_per_kg=4.0,
        status=ContractStatus.open,
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def _seed_graded_batch(db, *, crop="tomato", grade="A", kg=30.0) -> Batch:
    """A Batch parked at GRADED_FARM matching crop+grade."""
    farmer = Farmer(name="Grower", lat=18.0, lng=-76.8)
    db.add(farmer)
    db.flush()
    b = Batch(
        farmer_id=farmer.id,
        crop=crop,
        kg=kg,
        lat=farmer.lat,
        lng=farmer.lng,
        status=State.GRADED_FARM.value,
        farm_grade=grade,
        capture_token=f"tok-{farmer.id}-{crop}-{kg}",
        decay_on_handoff=False,
    )
    db.add(b)
    db.commit()
    db.refresh(b)
    return b


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_confirm_contract_ships_batches_and_creates_route(client):
    """Owning premium buyer confirms: batches pooled -> CONTRACTED -> SHIPPED,
    a Route exists, contract is fulfilling."""
    db = client.db_session()

    owner_buyer = _seed_buyer(db, name="SeaBreeze Resort", type_=BuyerType.premium)
    _seed_premium_user(db, email="resort@trace.demo", buyer=owner_buyer)
    contract = _seed_contract(db, buyer=owner_buyer)
    b1 = _seed_graded_batch(db, kg=30.0)
    b2 = _seed_graded_batch(db, kg=40.0)

    _login(client, "resort@trace.demo")
    resp = client.post(f"/contracts/{contract.id}/confirm")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["contract_id"] == contract.id
    assert body["shipped_batch_count"] == 2
    assert body["route_id"] is not None

    # Batches are now SHIPPED and linked to the route.
    db.refresh(b1)
    db.refresh(b2)
    assert b1.status == State.SHIPPED.value
    assert b2.status == State.SHIPPED.value
    assert b1.route_id == body["route_id"]
    assert b2.route_id == body["route_id"]

    # A Route for the contract's buyer exists with both batch ids + capacity.
    from app.models import Route

    route = db.get(Route, body["route_id"])
    assert route is not None
    assert route.buyer_id == owner_buyer.id
    assert sorted(route.batch_ids) == sorted([b1.id, b2.id])
    assert route.returning_leg_capacity == 70.0

    # Contract is now fulfilling.
    db.refresh(contract)
    assert contract.status == ContractStatus.fulfilling


def test_confirm_contract_403_for_non_owner_premium(client):
    """A different premium buyer (not the contract owner) is forbidden."""
    db = client.db_session()

    owner_buyer = _seed_buyer(db, name="Owner Resort", type_=BuyerType.premium)
    contract = _seed_contract(db, buyer=owner_buyer)
    _seed_graded_batch(db)

    other_buyer = _seed_buyer(db, name="Other Resort", type_=BuyerType.premium)
    _seed_premium_user(db, email="other@trace.demo", buyer=other_buyer)

    _login(client, "other@trace.demo")
    resp = client.post(f"/contracts/{contract.id}/confirm")
    assert resp.status_code == 403, resp.text


def test_confirm_contract_403_for_admin(client):
    """Admin is not a premium buyer -> require_buyer(premium) 403s."""
    db = client.db_session()

    owner_buyer = _seed_buyer(db, name="Owner Resort", type_=BuyerType.premium)
    contract = _seed_contract(db, buyer=owner_buyer)
    _seed_graded_batch(db)

    admin = User(
        email="admin@trace.demo",
        password_hash=hash_password(DEMO_PW),
        role=UserRole.admin,
        buyer_id=None,
    )
    db.add(admin)
    db.commit()

    _login(client, "admin@trace.demo")
    resp = client.post(f"/contracts/{contract.id}/confirm")
    assert resp.status_code == 403, resp.text


def test_confirm_contract_404_when_missing(client):
    """Unknown contract id -> 404."""
    db = client.db_session()
    owner_buyer = _seed_buyer(db, name="Resort", type_=BuyerType.premium)
    _seed_premium_user(db, email="resort@trace.demo", buyer=owner_buyer)

    _login(client, "resort@trace.demo")
    resp = client.post("/contracts/9999/confirm")
    assert resp.status_code == 404, resp.text


def test_confirm_contract_409_when_no_batches_pool(client):
    """Contract exists and is owned, but no GRADED_FARM batches match -> 409."""
    db = client.db_session()
    owner_buyer = _seed_buyer(db, name="Resort", type_=BuyerType.premium)
    _seed_premium_user(db, email="resort@trace.demo", buyer=owner_buyer)
    contract = _seed_contract(db, buyer=owner_buyer)
    # No batches seeded.

    _login(client, "resort@trace.demo")
    resp = client.post(f"/contracts/{contract.id}/confirm")
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"] == "no batches to pool"

"""Slice D — read endpoints (spec §4a visibility).

Exercises the four read-only list/dispute handlers that replace the 501 stubs:

* ``GET /batches``        — admin sees every batch.
* ``GET /offers``         — secondary buyer sees rerouted batches.
* ``GET /pickups``        — composter sees composted batches.
* ``GET /payouts``        — admin sees every payout.
* ``POST /batches/{id}/dispute`` — premium buyer disputes a DELIVERED batch.

Each test seeds the minimum rows it needs through the shared in-memory DB
exposed by the ``client`` fixture, then logs in via the auth router so the
TestClient holds the signed session cookie.
"""

from __future__ import annotations

import pytest

from app.auth import generate_capture_token, hash_password
from app.models import (
    Batch,
    Buyer,
    BuyerType,
    Farmer,
    MarketCategory,
    Payout,
    PayoutStatus,
    User,
    UserRole,
)
from app.statemachine import State

DEMO_PW = "pw"


# ---------------------------------------------------------------------------
# Seeding helpers
# ---------------------------------------------------------------------------


def _seed_user(db, *, email: str, role: UserRole, buyer=None) -> User:
    user = User(
        email=email,
        password_hash=hash_password(DEMO_PW),
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


def _make_batch(
    db,
    *,
    farmer: Farmer,
    crop: str = "tomato",
    kg: float = 30.0,
    status: State,
    farm_grade: str | None = "A",
    handoff_grade: str | None = None,
    decay_on_handoff: bool = False,
) -> Batch:
    """Insert a batch parked directly at ``status`` (seed-style bypass)."""
    batch = Batch(
        farmer_id=farmer.id,
        crop=crop,
        kg=kg,
        status=status.value,
        farm_grade=farm_grade,
        handoff_grade=handoff_grade,
        capture_token="pending",
    )
    db.add(batch)
    db.flush()
    generate_capture_token(db, batch)  # writes a real token + commits
    db.refresh(batch)
    return batch


def _login(client, email: str) -> None:
    resp = client.post("/auth/login", json={"email": email, "password": DEMO_PW})
    assert resp.status_code == 200, resp.text


# Fixed emails per role.
ADMIN_EMAIL = "admin@read.demo"
PREMIUM_EMAIL = "premium@read.demo"
SECONDARY_EMAIL = "secondary@read.demo"
COMPOSTER_EMAIL = "composter@read.demo"


def _seed_users(client) -> None:
    """Seed one user per role, each buyer linked to a Buyer row."""
    db = client.db_session()
    _seed_user(db, email=ADMIN_EMAIL, role=UserRole.admin)
    premium_buyer = _seed_buyer(db, name="Resort", type_=BuyerType.premium)
    _seed_user(db, email=PREMIUM_EMAIL, role=UserRole.premium_buyer, buyer=premium_buyer)
    secondary_buyer = _seed_buyer(db, name="School", type_=BuyerType.secondary)
    _seed_user(
        db, email=SECONDARY_EMAIL, role=UserRole.secondary_buyer, buyer=secondary_buyer
    )
    composter_buyer = _seed_buyer(db, name="Compost Co", type_=BuyerType.composter)
    _seed_user(
        db, email=COMPOSTER_EMAIL, role=UserRole.composter, buyer=composter_buyer
    )
    db.close()


# ---------------------------------------------------------------------------
# GET /batches — admin sees all
# ---------------------------------------------------------------------------


def test_batches_admin_sees_all(client):
    _seed_users(client)
    db = client.db_session()
    farmer = Farmer(name="Farm Alpha", telegram_chat_id="tg-1")
    db.add(farmer)
    db.commit()
    db.refresh(farmer)

    b1 = _make_batch(db, farmer=farmer, status=State.DELIVERED)
    b2 = _make_batch(db, farmer=farmer, status=State.COMPOSTED)
    expected_ids = {b1.id, b2.id}
    db.close()

    _login(client, ADMIN_EMAIL)
    resp = client.get("/batches")
    assert resp.status_code == 200
    rows = resp.json()
    assert isinstance(rows, list)
    assert len(rows) == 2
    ids = {r["id"] for r in rows}
    assert ids == expected_ids
    # Field contract (spec §4a).
    sample = rows[0]
    assert set(sample.keys()) == {
        "id",
        "farmer_id",
        "crop",
        "kg",
        "status",
        "farm_grade",
        "handoff_grade",
        "decay_on_handoff",
    }


def test_batches_forbidden_for_secondary_buyer(client):
    _seed_users(client)
    _login(client, SECONDARY_EMAIL)
    assert client.get("/batches").status_code == 403


# ---------------------------------------------------------------------------
# GET /offers — secondary buyer sees rerouted batches only
# ---------------------------------------------------------------------------


def test_offers_secondary_sees_rerouted_only(client):
    _seed_users(client)
    db = client.db_session()
    farmer = Farmer(name="Farm Bravo", telegram_chat_id="tg-2")
    db.add(farmer)
    db.commit()
    db.refresh(farmer)

    rerouted = _make_batch(db, farmer=farmer, status=State.REROUTED, kg=20.0, handoff_grade="B")
    delivered_sec = _make_batch(
        db, farmer=farmer, status=State.DELIVERED_SECONDARY, kg=10.0, handoff_grade="B"
    )
    expected_ids = {rerouted.id, delivered_sec.id}
    # Noise: other statuses must NOT appear in offers.
    _make_batch(db, farmer=farmer, status=State.DELIVERED)
    _make_batch(db, farmer=farmer, status=State.COMPOSTED)
    db.close()

    _login(client, SECONDARY_EMAIL)
    resp = client.get("/offers")
    assert resp.status_code == 200
    rows = resp.json()
    assert isinstance(rows, list)
    assert len(rows) == 2
    ids = {r["id"] for r in rows}
    assert ids == expected_ids
    # Field contract (spec §4a — grade+kg, no contract leak).
    sample = rows[0]
    assert set(sample.keys()) == {"id", "crop", "kg", "handoff_grade"}


# ---------------------------------------------------------------------------
# GET /pickups — composter sees composted batches only
# ---------------------------------------------------------------------------


def test_pickups_composter_sees_composted_only(client):
    _seed_users(client)
    db = client.db_session()
    farmer = Farmer(name="Farm Charlie", telegram_chat_id="tg-3")
    db.add(farmer)
    db.commit()
    db.refresh(farmer)

    composted = _make_batch(db, farmer=farmer, status=State.COMPOSTED, kg=15.0)
    expected_id = composted.id
    # Noise.
    _make_batch(db, farmer=farmer, status=State.DELIVERED)
    _make_batch(db, farmer=farmer, status=State.REROUTED)
    db.close()

    _login(client, COMPOSTER_EMAIL)
    resp = client.get("/pickups")
    assert resp.status_code == 200
    rows = resp.json()
    assert isinstance(rows, list)
    assert len(rows) == 1
    only = rows[0]
    assert only["id"] == expected_id
    assert set(only.keys()) == {"id", "crop", "kg"}


# ---------------------------------------------------------------------------
# GET /payouts — admin sees all payouts
# ---------------------------------------------------------------------------


def test_payouts_admin_sees_all(client):
    _seed_users(client)
    db = client.db_session()
    farmer = Farmer(name="Farm Delta", telegram_chat_id="tg-4")
    db.add(farmer)
    db.commit()
    db.refresh(farmer)

    batch = _make_batch(db, farmer=farmer, status=State.PAID)
    payout = Payout(
        farmer_id=farmer.id,
        batch_id=batch.id,
        grade_paid_at="A",
        destination="SeaBreeze Resort",
        market_category=MarketCategory.premium_market,
        kg=batch.kg,
        amount=120.00,
        status=PayoutStatus.held,
    )
    db.add(payout)
    db.commit()
    db.refresh(payout)
    payout_id = payout.id
    db.close()

    _login(client, ADMIN_EMAIL)
    resp = client.get("/payouts")
    assert resp.status_code == 200
    rows = resp.json()
    assert isinstance(rows, list)
    assert len(rows) == 1
    only = rows[0]
    assert only["id"] == payout_id
    assert set(only.keys()) == {
        "id",
        "farmer_id",
        "batch_id",
        "market_category",
        "kg",
        "amount",
        "status",
    }
    assert only["market_category"] == "premium_market"


# ---------------------------------------------------------------------------
# POST /batches/{id}/dispute
# ---------------------------------------------------------------------------


def test_dispute_on_delivered_batch_succeeds(client):
    _seed_users(client)
    db = client.db_session()
    farmer = Farmer(name="Farm Echo", telegram_chat_id="tg-5")
    db.add(farmer)
    db.commit()
    db.refresh(farmer)

    batch = _make_batch(db, farmer=farmer, status=State.DELIVERED)
    batch_id = batch.id
    db.close()

    _login(client, PREMIUM_EMAIL)
    resp = client.post(f"/batches/{batch_id}/dispute")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"batch_id": batch_id, "status": "DISPUTED"}

    # Persisted: a fresh read of the row is DISPUTED.
    db = client.db_session()
    refreshed = db.get(Batch, batch_id)
    assert refreshed.status == State.DISPUTED.value
    db.close()


def test_dispute_on_non_delivered_batch_is_409(client):
    _seed_users(client)
    db = client.db_session()
    farmer = Farmer(name="Farm Foxtrot", telegram_chat_id="tg-6")
    db.add(farmer)
    db.commit()
    db.refresh(farmer)

    # POOLED is not DELIVERED.
    batch = _make_batch(db, farmer=farmer, status=State.POOLED)
    batch_id = batch.id
    db.close()

    _login(client, PREMIUM_EMAIL)
    resp = client.post(f"/batches/{batch_id}/dispute")
    assert resp.status_code == 409
    assert resp.json()["detail"] == "only delivered batches can be disputed"


def test_dispute_missing_batch_is_404(client):
    _seed_users(client)
    _login(client, PREMIUM_EMAIL)
    resp = client.post("/batches/9999/dispute")
    assert resp.status_code == 404

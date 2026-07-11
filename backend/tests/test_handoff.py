"""Tests for the handoff step + scheduler (Slice D cascade orchestrator)."""

from __future__ import annotations

import base64

import pytest

from app.models import (
    Batch,
    Buyer,
    BuyerType,
    Contract,
    ContractStatus,
    Farmer,
    Payout,
    Route,
    RoutingDecision,
    VirtualShipment,
)
from app.services import ports
from app.services.handoff import run_handoff
from app.statemachine import State, transition


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_world(db) -> tuple[Contract, Buyer, Buyer, Buyer, Buyer]:
    """Premium buyer + contract, a secondary buyer, and 2 composters."""
    premium = Buyer(
        name="SeaBreeze Resort",
        type=BuyerType.premium,
        lat=18.0,
        lng=-76.8,
        demand_crop="tomato",
        demand_grade="A",
        demand_kg=500,
        price_per_kg=4.0,
        capacity=1000,
    )
    secondary = Buyer(
        name="Kingston School Feeding",
        type=BuyerType.secondary,
        lat=18.01,
        lng=-76.79,
        demand_crop="tomato",
        demand_grade="B",
        demand_kg=200,
        price_per_kg=1.7,
        capacity=200,
    )
    composter1 = Buyer(
        name="Hillside Compost",
        type=BuyerType.composter,
        lat=18.02,
        lng=-76.78,
        capacity=500,
    )
    composter2 = Buyer(
        name="GreenCycle Compost",
        type=BuyerType.composter,
        lat=18.05,
        lng=-76.75,
        capacity=500,
    )
    db.add_all([premium, secondary, composter1, composter2])
    db.flush()
    contract = Contract(
        buyer_id=premium.id,
        crop="tomato",
        grade="A",
        kg_target=200,
        price_per_kg=4.0,
        status=ContractStatus.fulfilling,
    )
    db.add(contract)
    db.flush()
    return contract, premium, secondary, composter1, composter2


def _shipped_batch(db, contract, *, decay: bool = False, kg: float = 30.0) -> Batch:
    farmer = Farmer(name="Test Farmer", telegram_chat_id="tg-handoff", lat=18.0, lng=-76.8)
    db.add(farmer)
    db.flush()
    shipment = VirtualShipment(contract_id=contract.id, total_kg=kg, status="open")
    db.add(shipment)
    db.flush()
    batch = Batch(
        farmer_id=farmer.id,
        crop="tomato",
        kg=kg,
        lat=18.0,
        lng=-76.8,
        status=State.GRADED_FARM.value,
        farm_grade="A",
        grade_reason_farm="fresh",
        capture_token="tok-handoff",
        virtual_shipment_id=shipment.id,
        decay_on_handoff=decay,
    )
    db.add(batch)
    db.flush()
    route = Route(
        buyer_id=contract.buyer_id,
        returning_leg_capacity=kg,
        batch_ids=[batch.id],
        washed_out=False,
    )
    db.add(route)
    db.flush()
    batch.route_id = route.id
    # Advance to SHIPPED so run_handoff can run (it expects GRADED_HANDOFF,
    # but it performs shipped->graded_handoff itself; here we ship first).
    transition(db, batch, State.POOLED)
    transition(db, batch, State.CONTRACTED, contract_id=contract.id)
    transition(db, batch, State.SHIPPED, route_id=route.id)
    db.commit()
    return batch


@pytest.fixture
def stub_ports(monkeypatch):
    """Make grade/decay/photo/message deterministic + observable."""
    calls: dict = {"sent": []}

    _PNG = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M8AAAMBAQDJ"
        "XIs2AAAAAElFTkSuQmCC"
    )

    def fake_photo(_batch):
        return _PNG

    def fake_decay(image):
        return image  # identity; the grade stub decides the grade

    def fake_grade(_image, _crop="tomato"):
        return {"grade": "B", "reason": "moderate blemishing, uneven color"}

    def fake_send(chat_id, event):
        calls["sent"].append((chat_id, event))

    monkeypatch.setattr(ports, "get_batch_photo", fake_photo)
    monkeypatch.setattr(ports, "simulate_decay", fake_decay)
    monkeypatch.setattr(ports, "grade", fake_grade)
    monkeypatch.setattr(ports, "send_farmer_update", fake_send)
    return calls


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_run_handoff_decay_a_to_b_routes_to_secondary(db_session, stub_ports):
    """A decay-flagged (A->B) batch reroutes to the secondary buyer."""
    contract, premium, secondary, comp1, comp2 = _make_world(db_session)
    batch = _shipped_batch(db_session, contract, decay=True)

    decision = run_handoff(db_session, batch)
    db_session.refresh(batch)

    # The batch was re-graded to B and rerouted to the secondary market.
    assert batch.handoff_grade == "B"
    assert batch.status == State.DELIVERED_SECONDARY.value
    assert decision is not None
    assert decision.reason_code.value == "transit_decay"
    assert decision.to_destination == secondary.name

    # A payout was created with the secondary market category + the farmer was
    # messaged — category only, never the destination name.
    payout = db_session.query(Payout).filter(Payout.batch_id == batch.id).one()
    assert payout.market_category.value == "secondary_market"
    assert float(payout.amount) == pytest.approx(30.0 * 1.7)
    assert len(stub_ports["sent"]) == 1
    _chat, event = stub_ports["sent"][0]
    assert event["market_category"] == "secondary_market"
    assert secondary.name not in str(event)


def test_run_handoff_no_decay_delivers_premium(db_session, monkeypatch):
    """A non-decay batch (grade holds at A) delivers premium, no reroute."""
    monkeypatch.setattr(ports, "get_batch_photo", lambda b: b"x")
    monkeypatch.setattr(ports, "simulate_decay", lambda im: im)
    monkeypatch.setattr(
        ports, "grade", lambda im, crop="tomato": {"grade": "A", "reason": "clean"}
    )
    sent = []
    monkeypatch.setattr(ports, "send_farmer_update", lambda c, e: sent.append((c, e)))

    contract, premium, secondary, comp1, comp2 = _make_world(db_session)
    batch = _shipped_batch(db_session, contract, decay=False)

    decision = run_handoff(db_session, batch)
    db_session.refresh(batch)

    assert batch.handoff_grade == "A"
    assert batch.status == State.DELIVERED.value
    assert decision is None  # happy path: no reroute decision row
    assert sent == []  # no farmer message on a clean delivery


def test_run_handoff_waste_goes_to_compost_zero_payout(db_session, monkeypatch):
    """A WASTE grade routes to compost with an explicit $0 payout."""
    monkeypatch.setattr(ports, "get_batch_photo", lambda b: b"x")
    monkeypatch.setattr(ports, "simulate_decay", lambda im: im)
    monkeypatch.setattr(
        ports, "grade", lambda im, crop="tomato": {"grade": "WASTE", "reason": "rot"}
    )
    monkeypatch.setattr(ports, "send_farmer_update", lambda c, e: None)

    contract, premium, secondary, comp1, comp2 = _make_world(db_session)
    batch = _shipped_batch(db_session, contract, decay=True)

    decision = run_handoff(db_session, batch)
    db_session.refresh(batch)

    assert batch.handoff_grade == "WASTE"
    assert batch.status == State.COMPOSTED.value
    payout = db_session.query(Payout).filter(Payout.batch_id == batch.id).one()
    assert payout.market_category.value == "composted"
    assert float(payout.amount) == 0.0

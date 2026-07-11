"""End-to-end cascade integration test (Slice D Definition of Done).

Exercises the WHOLE self-healing flow through the real service functions
(aggregation -> confirm/ship -> handoff re-grade -> decide_route -> payout),
plus the route-disruption anomaly — with the cross-slice ports (photo, grade,
decay, messaging) stubbed, since Slices B/C haven't landed yet.

This is the test that proves the demo cascade works before the other slices
merge.
"""

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
    ReasonCode,
)
from app.services import ports
from app.services.aggregation import pool_for_contract
from app.services.handoff import run_handoff
from app.statemachine import State, transition


_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M8AAAMBAQDJ"
    "XIs2AAAAAElFTkSuQmCC"
)


def _setup_demo_world(db):
    premium = Buyer(
        name="SeaBreeze Resort", type=BuyerType.premium, lat=18.0, lng=-76.8,
        demand_crop="tomato", demand_grade="A", demand_kg=500, price_per_kg=4.0, capacity=1000,
    )
    secondary = Buyer(
        name="Kingston School Feeding", type=BuyerType.secondary, lat=18.01, lng=-76.79,
        demand_crop="tomato", demand_grade="B", demand_kg=200, price_per_kg=1.7, capacity=200,
    )
    composter_primary = Buyer(
        name="Hillside Compost", type=BuyerType.composter, lat=18.02, lng=-76.78, capacity=500,
    )
    composter_fallback = Buyer(
        name="GreenCycle Compost", type=BuyerType.composter, lat=18.05, lng=-76.75, capacity=500,
    )
    db.add_all([premium, secondary, composter_primary, composter_fallback])
    db.flush()
    contract = Contract(
        buyer_id=premium.id, crop="tomato", grade="A",
        kg_target=200, price_per_kg=4.0, status=ContractStatus.open,
    )
    db.add(contract)
    db.flush()
    # Three graded farmers' batches ready to pool.
    farmers = []
    batches = []
    for i, kg in enumerate((30.0, 40.0, 50.0), start=1):
        f = Farmer(name=f"Farmer {i}", telegram_chat_id=f"tg-{i}", lat=18.0 + i * 0.001, lng=-76.8)
        db.add(f)
        db.flush()
        farmers.append(f)
        b = Batch(
            farmer_id=f.id, crop="tomato", kg=kg, lat=f.lat, lng=f.lng,
            status=State.GRADED_FARM.value, farm_grade="A", grade_reason_farm="fresh",
            capture_token=f"tok-cascade-{i}",
        )
        db.add(b)
        db.flush()
        batches.append(b)
    db.commit()
    return contract, batches, (premium, secondary, composter_primary, composter_fallback)


@pytest.fixture
def stub_ports(monkeypatch):
    monkeypatch.setattr(ports, "get_batch_photo", lambda b: _PNG)
    monkeypatch.setattr(ports, "simulate_decay", lambda im: im)
    monkeypatch.setattr(ports, "send_farmer_update", lambda c, e: None)


def test_full_cascade_transit_decay(db_session, stub_ports, monkeypatch):
    """Pool -> confirm/ship -> handoff(decay) -> reroute -> secondary payout."""
    # The handoff grade is B (decay); farmer grades were A at the farm.
    monkeypatch.setattr(
        ports, "grade", lambda im, crop="tomato": {"grade": "B", "reason": "blemished"}
    )

    contract, batches, (premium, secondary, cp, cf) = _setup_demo_world(db_session)

    # 1. Pool the three GRADED_FARM batches into the contract's shipment.
    shipment = pool_for_contract(db_session, contract)
    assert shipment is not None
    assert shipment.total_kg == pytest.approx(120.0)
    for b in batches:
        db_session.refresh(b)
        assert b.status == State.POOLED.value

    # 2. Confirm + ship: CONTRACTED then SHIPPED with a Route (mirrors the
    # /confirm handler).
    for b in batches:
        transition(db_session, b, State.CONTRACTED, contract_id=contract.id)
    route = Route(
        buyer_id=premium.id, returning_leg_capacity=120.0,
        batch_ids=[b.id for b in batches], washed_out=False,
    )
    db_session.add(route)
    db_session.flush()
    for b in batches:
        b.route_id = route.id
        b.decay_on_handoff = True  # mark one to decay (the demo's decay batch)
    db_session.commit()
    for b in batches:
        transition(db_session, b, State.SHIPPED, route_id=route.id)

    # 3. Run the handoff on the first batch (the scheduler would do this).
    target = batches[0]
    decision = run_handoff(db_session, target)
    db_session.refresh(target)

    # 4. The batch rerouted to the secondary market with a re-priced payout.
    assert target.status == State.DELIVERED_SECONDARY.value
    assert target.handoff_grade == "B"
    assert decision is not None
    assert decision.reason_code == ReasonCode.transit_decay
    payout = db_session.query(Payout).filter(Payout.batch_id == target.id).one()
    assert payout.market_category.value == "secondary_market"
    assert float(payout.amount) == pytest.approx(30.0 * 1.7)  # premium was 30*4.0


def test_route_disruption_anomaly(db_session, stub_ports, monkeypatch):
    """A WASTE batch whose primary route is washed out reroutes to the
    fallback composter (reason_code=route_disruption)."""
    # The handoff grade is WASTE.
    monkeypatch.setattr(
        ports, "grade", lambda im, crop="tomato": {"grade": "WASTE", "reason": "rot"}
    )

    contract, batches, (premium, secondary, cp, cf) = _setup_demo_world(db_session)
    pool_for_contract(db_session, contract)
    target = batches[0]
    transition(db_session, target, State.CONTRACTED, contract_id=contract.id)
    route = Route(
        buyer_id=premium.id, returning_leg_capacity=target.kg,
        batch_ids=[target.id], washed_out=True,  # primary route WASHED OUT
    )
    db_session.add(route)
    db_session.flush()
    target.route_id = route.id
    target.decay_on_handoff = True
    db_session.commit()
    transition(db_session, target, State.SHIPPED, route_id=route.id)

    decision = run_handoff(db_session, target)
    db_session.refresh(target)

    # The batch rerouted to a fallback composter due to the route disruption.
    assert target.status == State.COMPOSTED.value
    assert decision is not None
    assert decision.reason_code == ReasonCode.route_disruption
    assert decision.to_destination in {cp.name, cf.name}  # a composter, not premium/secondary
    payout = db_session.query(Payout).filter(Payout.batch_id == target.id).one()
    assert payout.market_category.value == "composted"
    assert float(payout.amount) == 0.0

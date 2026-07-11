"""Table-driven tests for routing decisions + payout math (spec §10, §11).

These are pure DB + rules tests: no mocking. Each test constructs the minimal
rows (a farmer, the four buyer types, a contract, a virtual shipment, a route,
a batch parked at GRADED_HANDOFF) and asserts that ``decide_route`` drives the
batch to the correct terminal state, writes the right ``RoutingDecision`` row
(if any), and that ``compute_payout`` (called internally) leaves the right
``Payout`` row.

Branches exercised (one test each, per spec §10):
  1. no-decay      — handoff grade == farm grade  -> delivered, no reroute row
  2. a_to_b        — A -> B downgrade             -> rerouted -> delivered_secondary
  3. waste         — handoff WASTE                -> composted, payout 0.00
  4. route_disruption — Route.washed_out == True  -> fallback composter
  5. no_capacity   — no reachable secondary       -> lost (no composter reachable)
"""

from __future__ import annotations

import uuid

import pytest

from app.models import (
    Batch,
    Buyer,
    BuyerType,
    Contract,
    ContractStatus,
    Farmer,
    PayoutStatus,
    ReasonCode,
    Route,
    VirtualShipment,
    MarketCategory,
    RoutingDecision,
)
from app.services.routing import decide_route, compute_payout
from app.statemachine import State


# ---------------------------------------------------------------------------
# Helpers — build the standard buyer/contract/shipment/route scaffolding.
# ---------------------------------------------------------------------------


def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _make_farmer(db) -> Farmer:
    farmer = Farmer(name=f"farmer-{_uid()}", lat=18.00, lng=-76.80)
    db.add(farmer)
    db.flush()
    return farmer


def _make_buyers(db) -> dict[str, Buyer]:
    """Create the standard 4 buyers: premium, secondary, 2 composters."""
    premium = Buyer(
        name=f"resort-{_uid()}",
        type=BuyerType.premium,
        lat=18.01,
        lng=-76.82,
        demand_crop="tomato",
        demand_grade="A",
        demand_kg=200.0,
        price_per_kg=4.0,
        capacity=500.0,
    )
    secondary = Buyer(
        name=f"school-{_uid()}",
        type=BuyerType.secondary,
        lat=18.02,
        lng=-76.81,
        demand_crop="tomato",
        demand_grade="B",
        demand_kg=150.0,
        price_per_kg=2.0,
        capacity=400.0,
    )
    composter = Buyer(
        name=f"compost-{_uid()}",
        type=BuyerType.composter,
        lat=18.03,
        lng=-76.80,
        demand_crop="tomato",
        demand_grade=None,
        demand_kg=100.0,
        price_per_kg=0.5,
        capacity=300.0,
    )
    fallback = Buyer(
        name=f"fallback-compost-{_uid()}",
        type=BuyerType.composter,
        lat=18.04,
        lng=-76.79,
        demand_crop="tomato",
        demand_grade=None,
        demand_kg=100.0,
        price_per_kg=0.5,
        capacity=300.0,
    )
    db.add_all([premium, secondary, composter, fallback])
    db.flush()
    return {
        "premium": premium,
        "secondary": secondary,
        "composter": composter,
        "fallback": fallback,
    }


def _make_contract(db, premium_buyer: Buyer) -> Contract:
    contract = Contract(
        buyer_id=premium_buyer.id,
        crop="tomato",
        grade="A",
        kg_target=200.0,
        price_per_kg=4.0,
        deadline=None,
        status=ContractStatus.open,
    )
    db.add(contract)
    db.flush()
    return contract


def _make_shipment(db, contract: Contract, total_kg: float = 30.0) -> VirtualShipment:
    shipment = VirtualShipment(
        contract_id=contract.id,
        total_kg=total_kg,
        status="open",
    )
    db.add(shipment)
    db.flush()
    return shipment


def _make_route(
    db,
    buyer: Buyer,
    *,
    washed_out: bool = False,
    returning_leg_capacity: float | None = 100.0,
) -> Route:
    route = Route(
        buyer_id=buyer.id,
        pickup_geo="18.00,-76.80",
        returning_leg_capacity=returning_leg_capacity,
        batch_ids=[],
        washed_out=washed_out,
    )
    db.add(route)
    db.flush()
    return route


def _make_batch(
    db,
    farmer: Farmer,
    *,
    farm_grade: str = "A",
    handoff_grade: str | None = None,
    route: Route | None = None,
    shipment: VirtualShipment | None = None,
    kg: float = 30.0,
) -> Batch:
    batch = Batch(
        farmer_id=farmer.id,
        crop="tomato",
        kg=kg,
        lat=farmer.lat,
        lng=farmer.lng,
        status=State.GRADED_HANDOFF.value,
        farm_grade=farm_grade,
        handoff_grade=handoff_grade,
        final_grade=None,
        decay_event=None,
        photo_ref=None,
        grade_reason_farm="ok",
        grade_reason_handoff=None,
        capture_token=f"tok-{_uid()}",
        capture_token_expires_at=None,
        decay_on_handoff=False,
        route_id=route.id if route else None,
        virtual_shipment_id=shipment.id if shipment else None,
    )
    db.add(batch)
    db.commit()
    return batch


def _all_buyers(db) -> list[Buyer]:
    return db.query(Buyer).all()


# ---------------------------------------------------------------------------
# Branch 1: no decay (handoff == farm) -> delivered, no reroute decision row.
# ---------------------------------------------------------------------------


def test_no_decay_delivered_no_reroute_row(db_session):
    farmer = _make_farmer(db_session)
    buyers = _make_buyers(db_session)
    contract = _make_contract(db_session, buyers["premium"])
    shipment = _make_shipment(db_session, contract)
    route = _make_route(db_session, buyers["premium"])
    batch = _make_batch(
        db_session,
        farmer,
        farm_grade="A",
        handoff_grade="A",
        route=route,
        shipment=shipment,
    )

    decision = decide_route(
        db_session, batch, handoff_grade="A", contract=contract, buyers=_all_buyers(db_session)
    )

    assert batch.status == State.DELIVERED.value
    # No reroute decision row for the unchanged case.
    assert decision is None
    rows = db_session.query(RoutingDecision).filter_by(batch_id=batch.id).all()
    assert rows == []


# ---------------------------------------------------------------------------
# Branch 2: A -> B downgrade -> rerouted -> delivered_secondary.
# ---------------------------------------------------------------------------


def test_a_to_b_downgrade_routes_to_secondary(db_session):
    farmer = _make_farmer(db_session)
    buyers = _make_buyers(db_session)
    contract = _make_contract(db_session, buyers["premium"])
    shipment = _make_shipment(db_session, contract)
    route = _make_route(db_session, buyers["premium"])
    batch = _make_batch(
        db_session,
        farmer,
        farm_grade="A",
        handoff_grade="B",
        route=route,
        shipment=shipment,
    )

    decision = decide_route(
        db_session, batch, handoff_grade="B", contract=contract, buyers=_all_buyers(db_session)
    )

    assert batch.status == State.DELIVERED_SECONDARY.value
    assert decision is not None
    assert decision.reason_code == ReasonCode.transit_decay
    assert decision.to_destination == buyers["secondary"].name
    # Batch pulled from the premium shipment.
    db_session.refresh(batch)
    assert batch.virtual_shipment_id is None
    # Payout row: secondary market, amount = kg * price.
    from app.models import Payout

    p = db_session.query(Payout).filter_by(batch_id=batch.id).one()
    assert p.market_category == MarketCategory.secondary_market
    assert p.kg == 30.0
    # 30kg * 2.0/kg = 60.00
    assert float(p.amount) == 60.00


# ---------------------------------------------------------------------------
# Branch 3: WASTE -> composted, payout amount 0.00.
# ---------------------------------------------------------------------------


def test_waste_routes_to_composted_zero_payout(db_session):
    farmer = _make_farmer(db_session)
    buyers = _make_buyers(db_session)
    contract = _make_contract(db_session, buyers["premium"])
    shipment = _make_shipment(db_session, contract)
    route = _make_route(db_session, buyers["premium"])
    batch = _make_batch(
        db_session,
        farmer,
        farm_grade="A",
        handoff_grade="WASTE",
        route=route,
        shipment=shipment,
    )

    decision = decide_route(
        db_session, batch, handoff_grade="WASTE", contract=contract, buyers=_all_buyers(db_session)
    )

    assert batch.status == State.COMPOSTED.value
    assert decision is not None
    assert decision.reason_code == ReasonCode.transit_decay

    from app.models import Payout

    p = db_session.query(Payout).filter_by(batch_id=batch.id).one()
    assert p.market_category == MarketCategory.composted
    assert p.status == PayoutStatus.released
    assert float(p.amount) == 0.00


# ---------------------------------------------------------------------------
# Branch 4: route disruption (Route.washed_out=True) -> fallback composter.
# ---------------------------------------------------------------------------


def test_route_disruption_falls_back_to_composter(db_session):
    farmer = _make_farmer(db_session)
    buyers = _make_buyers(db_session)
    contract = _make_contract(db_session, buyers["premium"])
    shipment = _make_shipment(db_session, contract)
    route = _make_route(db_session, buyers["premium"], washed_out=True)
    batch = _make_batch(
        db_session,
        farmer,
        farm_grade="A",
        handoff_grade="A",
        route=route,
        shipment=shipment,
    )

    decision = decide_route(
        db_session, batch, handoff_grade="A", contract=contract, buyers=_all_buyers(db_session)
    )

    # Washed-out route forces the disruption branch regardless of grade match.
    assert batch.status in (State.DELIVERED_SECONDARY.value, State.COMPOSTED.value)
    assert decision is not None
    assert decision.reason_code == ReasonCode.route_disruption
    # Destination must be one of the composters (buyer type).
    assert decision.to_destination in (
        buyers["composter"].name,
        buyers["fallback"].name,
    )


# ---------------------------------------------------------------------------
# Branch 5: no reachable secondary with capacity -> lost.
# ---------------------------------------------------------------------------


def test_no_capacity_secondary_routes_to_lost(db_session):
    farmer = _make_farmer(db_session)
    # Create buyers but no secondary buyer matching crop (so none reachable).
    premium = Buyer(
        name=f"resort-{_uid()}",
        type=BuyerType.premium,
        lat=18.01,
        lng=-76.82,
        demand_crop="tomato",
        demand_grade="A",
        demand_kg=200.0,
        price_per_kg=4.0,
        capacity=500.0,
    )
    # A composter placed FAR away so it is NOT reachable on the returning leg.
    far_composter = Buyer(
        name=f"far-compost-{_uid()}",
        type=BuyerType.composter,
        lat=0.0,
        lng=0.0,  # thousands of km from the route buyer
        demand_crop="tomato",
        demand_grade=None,
        demand_kg=100.0,
        price_per_kg=0.5,
        capacity=300.0,
    )
    db_session.add_all([premium, far_composter])
    db_session.flush()
    contract = _make_contract(db_session, premium)
    shipment = _make_shipment(db_session, premium)
    route = _make_route(db_session, premium)
    batch = _make_batch(
        db_session,
        farmer,
        farm_grade="A",
        handoff_grade="B",
        route=route,
        shipment=shipment,
    )

    decide_route(
        db_session,
        batch,
        handoff_grade="B",
        contract=contract,
        buyers=_all_buyers(db_session),
    )

    # No secondary reachable, no reachable composter -> LOST.
    assert batch.status == State.LOST.value


# ---------------------------------------------------------------------------
# compute_payout unit tests (direct calls).
# ---------------------------------------------------------------------------


def test_compute_payout_premium_market_held_real(db_session):
    """A premium-destination payout is non-zero and category premium_market."""
    from app.models import Payout

    farmer = _make_farmer(db_session)
    buyer = Buyer(
        name=f"resort-{_uid()}",
        type=BuyerType.premium,
        lat=18.01,
        lng=-76.82,
        demand_crop="tomato",
        demand_grade="A",
        price_per_kg=4.0,
        capacity=500.0,
    )
    db_session.add(buyer)
    db_session.flush()
    batch = Batch(
        farmer_id=farmer.id,
        crop="tomato",
        kg=25.0,
        lat=farmer.lat,
        lng=farmer.lng,
        status=State.DELIVERED.value,
        farm_grade="A",
        handoff_grade="A",
        capture_token=f"tok-{_uid()}",
    )
    db_session.add(batch)
    db_session.commit()

    p = compute_payout(db_session, batch, buyer, price_per_kg=4.0)

    assert p.market_category == MarketCategory.premium_market
    assert p.kg == 25.0
    assert float(p.amount) == 100.00
    assert p.destination == buyer.name
    assert p.grade_paid_at == "A"


def test_compute_payout_composter_zero_amount_released(db_session):
    """A composter-destination payout is explicitly zero and released."""
    from app.models import Payout

    farmer = _make_farmer(db_session)
    buyer = Buyer(
        name=f"compost-{_uid()}",
        type=BuyerType.composter,
        lat=18.03,
        lng=-76.80,
        demand_crop="tomato",
        price_per_kg=0.5,
        capacity=300.0,
    )
    db_session.add(buyer)
    db_session.flush()
    batch = Batch(
        farmer_id=farmer.id,
        crop="tomato",
        kg=40.0,
        lat=farmer.lat,
        lng=farmer.lng,
        status=State.COMPOSTED.value,
        farm_grade="B",
        handoff_grade="WASTE",
        capture_token=f"tok-{_uid()}",
    )
    db_session.add(batch)
    db_session.commit()

    p = compute_payout(db_session, batch, buyer, price_per_kg=0.5)

    assert p.market_category == MarketCategory.composted
    assert p.status == PayoutStatus.released
    assert float(p.amount) == 0.00

"""Smoke test: one row of each table type commits cleanly.

This is a schema-integrity check, not a behavior test. It inserts a valid,
minimally-populated row for each of the 11 tables (with correct FK targets and
required fields), commits, and asserts they all received an id (or PK) back
from the DB. The state machine, grading, and routing behavior are tested
elsewhere.
"""

from datetime import datetime, timedelta, timezone

from app.models import (
    AuditEvent,
    Batch,
    Buyer,
    Contract,
    ContractStatus,
    Farmer,
    MarketCategory,
    Payout,
    PayoutStatus,
    ReasonCode,
    Route,
    RoutingDecision,
    User,
    UserRole,
    VirtualShipment,
    VirtualShipmentBatch,
    BuyerType,
)


def test_all_models_commit(db_session):
    """Insert one of every row type; commit must succeed with valid FKs."""
    # --- independent parents first ---
    buyer = Buyer(
        name="Premium Co",
        type=BuyerType.premium,
        lat=1.35,
        lng=103.82,
        demand_crop="tomato",
        demand_grade="A",
        demand_kg=100.0,
        price_per_kg=4.0,
        capacity=500.0,
    )
    farmer = Farmer(name="Siti", telegram_chat_id="12345", lat=1.30, lng=103.85)
    db_session.add_all([buyer, farmer])
    db_session.flush()  # assign ids without committing

    # --- User links to Buyer (nullable FK populated) ---
    user = User(
        email="buyer@trace.local",
        password_hash="$2b$12$hashplaceholder",
        role=UserRole.premium_buyer,
        buyer_id=buyer.id,
    )
    db_session.add(user)

    # --- Contract -> Buyer ---
    contract = Contract(
        buyer_id=buyer.id,
        crop="tomato",
        grade="A",
        kg_target=100.0,
        price_per_kg=4.0,
        deadline=datetime.now(timezone.utc) + timedelta(days=7),
        status=ContractStatus.open,
    )
    db_session.add(contract)
    db_session.flush()

    # --- VirtualShipment -> Contract ---
    shipment = VirtualShipment(
        contract_id=contract.id,
        total_kg=80.0,
        status="open",
    )
    db_session.add(shipment)
    db_session.flush()

    # --- Route -> Buyer ---
    route = Route(
        buyer_id=buyer.id,
        pickup_geo="1.30,103.85",
        returning_leg_capacity=50.0,
        batch_ids=[],
    )
    db_session.add(route)
    db_session.flush()

    # --- Batch -> Farmer (+ optional shipment/route) ---
    batch = Batch(
        farmer_id=farmer.id,
        crop="tomato",
        kg=20.0,
        lat=1.30,
        lng=103.85,
        status="HARVESTED",  # set only by state machine in prod; raw here
        farm_grade="A",
        handoff_grade=None,
        final_grade=None,
        decay_event=None,
        photo_ref="photos/farm/abc.jpg",
        grade_reason_farm="uniform ripe, no damage",
        grade_reason_handoff=None,
        capture_token="token-batch-1",
        capture_token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        virtual_shipment_id=shipment.id,
        route_id=route.id,
        decay_on_handoff=False,
    )
    db_session.add(batch)
    db_session.flush()

    # --- link table: VirtualShipmentBatch ---
    vsb = VirtualShipmentBatch(
        shipment_id=shipment.id,
        batch_id=batch.id,
        pct_contribution=25.0,
    )
    db_session.add(vsb)

    # --- RoutingDecision -> Batch ---
    routing_decision = RoutingDecision(
        batch_id=batch.id,
        from_destination="premium_buyer",
        to_destination="secondary_buyer",
        reason_code=ReasonCode.transit_decay,
        claude_justification="Handoff grade dropped A->B; rerouted to secondary.",
    )
    db_session.add(routing_decision)

    # --- Payout -> Farmer + Batch ---
    payout = Payout(
        farmer_id=farmer.id,
        batch_id=batch.id,
        grade_paid_at="A",
        destination="premium_market",
        market_category=MarketCategory.premium_market,
        kg=20.0,
        amount=80.00,
        status=PayoutStatus.held,
    )
    db_session.add(payout)

    # --- AuditEvent -> Batch (nullable FK, populated) ---
    audit_event = AuditEvent(
        batch_id=batch.id,
        event_type="batch.created",
        payload={"source": "telegram", "chat_id": "12345"},
    )
    db_session.add(audit_event)

    # The single commit exercising every table.
    db_session.commit()

    # --- assertions: every row got a primary key back ---
    assert user.id is not None
    assert buyer.id is not None
    assert farmer.id is not None
    assert contract.id is not None
    assert batch.id is not None
    assert shipment.id is not None
    assert vsb.shipment_id == shipment.id and vsb.batch_id == batch.id
    assert route.id is not None
    assert routing_decision.id is not None
    assert payout.id is not None
    assert audit_event.id is not None

    # decay_on_handoff default is honored.
    db_session.refresh(batch)
    assert batch.decay_on_handoff is False

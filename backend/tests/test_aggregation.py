"""Tests for the aggregation service (spec §4b) — pooling + demand feed.

Covers:

1. ``pool_for_contract``: matching GRADED_FARM batches (same crop+grade) are
   gathered into a single VirtualShipment, each batch's pct_contribution is its
   share of the total (summing to ~1.0), every batch is transitioned to POOLED
   via the state machine, and each batch.virtual_shipment_id points at the new
   shipment. A contract with no matching batches returns None.

2. ``demand_feed``: open contracts are surfaced as anonymized demand dicts —
   crop, grade, qty_band, urgency only. No buyer name, buyer id, contract id, or
   price ever appears (spec §4a visibility rule).
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.models import (
    Batch,
    Buyer,
    Contract,
    ContractStatus,
    Farmer,
    VirtualShipment,
    VirtualShipmentBatch,
)
from app.services.aggregation import demand_feed, pool_for_contract
from app.statemachine import State


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_farmer(db_session, name="farmer-1") -> Farmer:
    farmer = Farmer(name=name, lat=1.30, lng=103.85)
    db_session.add(farmer)
    db_session.flush()
    return farmer


def _make_graded_batch(
    db_session,
    *,
    farmer_id: int,
    crop: str,
    grade: str,
    kg: float,
    token: str,
) -> Batch:
    """A batch sitting in GRADED_FARM with a farm_grade set."""
    batch = Batch(
        farmer_id=farmer_id,
        crop=crop,
        kg=kg,
        status=State.GRADED_FARM.value,
        farm_grade=grade,
        capture_token=token,
    )
    db_session.add(batch)
    db_session.flush()
    return batch


def _make_contract(
    db_session,
    *,
    crop: str,
    grade: str,
    kg_target: float,
    status: ContractStatus = ContractStatus.open,
    deadline: datetime | None = None,
) -> Contract:
    buyer = Buyer(
        name="Premium Co.",
        type="premium",
        demand_crop=crop,
        demand_grade=grade,
    )
    db_session.add(buyer)
    db_session.flush()
    contract = Contract(
        buyer_id=buyer.id,
        crop=crop,
        grade=grade,
        kg_target=kg_target,
        price_per_kg=5.0,
        deadline=deadline,
        status=status,
    )
    db_session.add(contract)
    db_session.commit()
    return contract


# ---------------------------------------------------------------------------
# pool_for_contract
# ---------------------------------------------------------------------------


def test_pool_for_contract_creates_shipment_and_transitions_batches(db_session):
    """Three matching GRADED_FARM batches pool into one shipment."""
    farmer = _make_farmer(db_session)
    b1 = _make_graded_batch(
        db_session, farmer_id=farmer.id, crop="tomato", grade="A", kg=10.0, token="tok-1"
    )
    b2 = _make_graded_batch(
        db_session, farmer_id=farmer.id, crop="tomato", grade="A", kg=20.0, token="tok-2"
    )
    b3 = _make_graded_batch(
        db_session, farmer_id=farmer.id, crop="tomato", grade="A", kg=30.0, token="tok-3"
    )
    db_session.commit()

    contract = _make_contract(db_session, crop="tomato", grade="A", kg_target=100.0)

    shipment = pool_for_contract(db_session, contract)

    # One shipment created against this contract, total kg sums the batches.
    assert isinstance(shipment, VirtualShipment)
    assert shipment.contract_id == contract.id
    assert shipment.status == "open"
    assert shipment.total_kg == pytest.approx(60.0)

    # Each batch is now POOLED, linked to the shipment, and has a contribution.
    db_session.refresh(b1)
    db_session.refresh(b2)
    db_session.refresh(b3)
    for b in (b1, b2, b3):
        assert b.status == State.POOLED.value
        assert b.virtual_shipment_id == shipment.id

    links = (
        db_session.query(VirtualShipmentBatch)
        .filter(VirtualShipmentBatch.shipment_id == shipment.id)
        .all()
    )
    assert len(links) == 3
    total_pct = sum(link.pct_contribution for link in links)
    assert total_pct == pytest.approx(1.0)

    # Individual contributions are proportional to kg.
    by_batch = {link.batch_id: link.pct_contribution for link in links}
    assert by_batch[b1.id] == pytest.approx(10.0 / 60.0)
    assert by_batch[b2.id] == pytest.approx(20.0 / 60.0)
    assert by_batch[b3.id] == pytest.approx(30.0 / 60.0)


def test_pool_for_contract_returns_none_when_no_matching_batches(db_session):
    """A contract with no eligible GRADED_FARM batches yields no shipment."""
    contract = _make_contract(db_session, crop="chilli", grade="B", kg_target=50.0)
    assert pool_for_contract(db_session, contract) is None


def test_pool_for_contract_skips_non_matching_crop_or_grade(db_session):
    """Batches of a different crop or grade are not gathered."""
    farmer = _make_farmer(db_session)
    # Matching.
    _make_graded_batch(
        db_session, farmer_id=farmer.id, crop="tomato", grade="A", kg=15.0, token="tok-m1"
    )
    # Wrong crop.
    _make_graded_batch(
        db_session, farmer_id=farmer.id, crop="chilli", grade="A", kg=5.0, token="tok-x1"
    )
    # Wrong grade.
    _make_graded_batch(
        db_session, farmer_id=farmer.id, crop="tomato", grade="B", kg=5.0, token="tok-x2"
    )
    db_session.commit()

    contract = _make_contract(db_session, crop="tomato", grade="A", kg_target=100.0)
    shipment = pool_for_contract(db_session, contract)

    assert shipment is not None
    assert shipment.total_kg == pytest.approx(15.0)
    links = (
        db_session.query(VirtualShipmentBatch)
        .filter(VirtualShipmentBatch.shipment_id == shipment.id)
        .all()
    )
    assert len(links) == 1


# ---------------------------------------------------------------------------
# demand_feed
# ---------------------------------------------------------------------------


def test_demand_feed_returns_anonymized_rows(db_session):
    """Open contracts surface as anonymized demand with no buyer/price/id fields."""
    _make_contract(
        db_session, crop="tomato", grade="A", kg_target=120.0
    )  # medium band
    _make_contract(
        db_session, crop="chilli", grade="B", kg_target=30.0
    )  # small band
    _make_contract(
        db_session, crop="kale", grade="A", kg_target=300.0
    )  # large band

    feed = demand_feed(db_session)

    assert isinstance(feed, list)
    assert len(feed) == 3

    # Every row has exactly the anonymized keys — nothing identifying.
    allowed_keys = {"crop", "grade", "qty_band", "urgency"}
    for row in feed:
        assert set(row.keys()) == allowed_keys
        assert row["qty_band"] in {"small", "medium", "large"}
        assert row["urgency"] in {"urgent", "standard", "open"}

    bands = {row["crop"]: row["qty_band"] for row in feed}
    assert bands["tomato"] == "medium"
    assert bands["chilli"] == "small"
    assert bands["kale"] == "large"


def test_demand_feed_qty_band_boundaries(db_session):
    """Bucket boundaries: <50 small, 50-200 medium, >200 large."""
    _make_contract(db_session, crop="c1", grade="A", kg_target=49.0)   # small
    _make_contract(db_session, crop="c2", grade="A", kg_target=50.0)   # medium
    _make_contract(db_session, crop="c3", grade="A", kg_target=200.0)  # medium
    _make_contract(db_session, crop="c4", grade="A", kg_target=201.0)  # large

    feed = demand_feed(db_session)
    by_crop = {row["crop"]: row["qty_band"] for row in feed}
    assert by_crop["c1"] == "small"
    assert by_crop["c2"] == "medium"
    assert by_crop["c3"] == "medium"
    assert by_crop["c4"] == "large"


def test_demand_feed_urgency_from_deadline(db_session):
    """A deadline within 2 days is 'urgent'; further out is 'standard'; none is 'open'."""
    now = datetime.utcnow()
    _make_contract(
        db_session, crop="soon", grade="A", kg_target=10.0,
        deadline=now + timedelta(hours=12),
    )
    _make_contract(
        db_session, crop="later", grade="A", kg_target=10.0,
        deadline=now + timedelta(days=5),
    )
    _make_contract(db_session, crop="none", grade="A", kg_target=10.0)  # no deadline

    feed = demand_feed(db_session)
    by_crop = {row["crop"]: row["urgency"] for row in feed}
    assert by_crop["soon"] == "urgent"
    assert by_crop["later"] == "standard"
    assert by_crop["none"] == "open"


def test_demand_feed_excludes_non_open_contracts(db_session):
    """Only open contracts appear in the feed."""
    _make_contract(
        db_session, crop="open-crop", grade="A", kg_target=10.0,
        status=ContractStatus.open,
    )
    _make_contract(
        db_session, crop="fulfilled-crop", grade="A", kg_target=10.0,
        status=ContractStatus.fulfilled,
    )

    feed = demand_feed(db_session)
    crops = {row["crop"] for row in feed}
    assert crops == {"open-crop"}

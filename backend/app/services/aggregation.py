"""Aggregation service (spec §4b) — pools farm-graded batches against contracts
and surfaces anonymized buyer demand to the farmer-facing layer.

Two functions:

* :func:`pool_for_contract` — gather GRADED_FARM batches matching a contract's
  crop+grade into a :class:`VirtualShipment`, recording each batch's pct
  contribution and transitioning every batch to POOLED through the state
  machine (the only sanctioned way to mutate ``Batch.status``).

* :func:`demand_feed` — return open contracts as anonymized demand dicts
  (crop, grade, qty_band, urgency). No buyer name, id, price, or contract id
  ever leaks — this is the farmer-facing side of the visibility rule (§4a).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Batch,
    Contract,
    ContractStatus,
    VirtualShipment,
    VirtualShipmentBatch,
)
from app.statemachine import State, transition

# Deadline window for the "urgent" urgency band in demand_feed.
_URGENT_WINDOW = timedelta(days=2)


def pool_for_contract(db: Session, contract: Contract) -> VirtualShipment | None:
    """Gather eligible GRADED_FARM batches into a VirtualShipment for ``contract``.

    Eligibility (MVP): ``status == GRADED_FARM`` AND ``crop == contract.crop``
    AND ``farm_grade == contract.grade``.

    NOTE: strict geo-box filtering (batches within a reasonable radius of the
    contract's buyer / pickup region) is intentionally skipped for the MVP — we
    match on crop+grade only. A future revision would add a lat/lng bounding-box
    filter here, e.g. ``Batch.lat.between(lo_lat, hi_lat)`` narrowed by the
    contract's buyer geography. Leaving this as crop+grade keeps the demo flow
    deterministic.

    Returns None when no batches match.

    For each matched batch this: creates a
    :class:`VirtualShipmentBatch` row holding its pct_contribution, sets
    ``batch.virtual_shipment_id``, and runs the ``GRADED_FARM -> POOLED``
    transition through the state machine. The shipment and all link rows are
    committed together.
    """
    batches = (
        db.execute(
            select(Batch).where(
                Batch.status == State.GRADED_FARM.value,
                Batch.crop == contract.crop,
                Batch.farm_grade == contract.grade,
            )
        )
        .scalars()
        .all()
    )

    if not batches:
        return None

    total_kg = sum(b.kg for b in batches)

    shipment = VirtualShipment(
        contract_id=contract.id,
        total_kg=total_kg,
        status="open",
    )
    db.add(shipment)
    db.flush()  # populate shipment.id

    for batch in batches:
        link = VirtualShipmentBatch(
            shipment_id=shipment.id,
            batch_id=batch.id,
            pct_contribution=(batch.kg / total_kg) if total_kg else 0.0,
        )
        db.add(link)
        batch.virtual_shipment_id = shipment.id
        transition(db, batch, State.POOLED)

    db.commit()
    return shipment


def demand_feed(db: Session) -> list[dict]:
    """Return open contracts as anonymized demand for the farmer-facing layer.

    Each item is ``{crop, grade, qty_band, urgency}`` — never buyer name, buyer
    id, contract id, or price (spec §4a visibility rule).

    qty_band buckets ``kg_target``: <50 -> "small", 50-200 -> "medium",
    >200 -> "large".

    urgency: if the contract has no deadline -> "open"; if the deadline is
    within :data:`_URGENT_WINDOW` (2 days) of now -> "urgent"; otherwise
    "standard".
    """
    contracts = (
        db.execute(
            select(Contract).where(Contract.status == ContractStatus.open)
        )
        .scalars()
        .all()
    )

    now = datetime.utcnow()
    feed: list[dict] = []
    for c in contracts:
        feed.append(
            {
                "crop": c.crop,
                "grade": c.grade,
                "qty_band": _qty_band(c.kg_target),
                "urgency": _urgency(c.deadline, now),
            }
        )
    return feed


def _qty_band(kg_target: float) -> str:
    if kg_target < 50:
        return "small"
    if kg_target <= 200:
        return "medium"
    return "large"


def _urgency(deadline: datetime | None, now: datetime) -> str:
    if deadline is None:
        return "open"
    if deadline - now <= _URGENT_WINDOW:
        return "urgent"
    return "standard"

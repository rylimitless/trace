"""The handoff step — Slice D owns the entire post-shipment re-grade.

``run_handoff`` is the cascade orchestrator: it pulls the stored farm photo
(Slice B port), optionally degrades it (Slice C port), re-grades (Slice C
port), writes ``handoff_grade``, advances ``shipped -> graded_handoff``, then
hands off to :func:`app.services.routing.decide_route` (which runs the reroute
branch + payout internally), generates a justification, and messages the
farmer (Slice B port) — all grade + outcome + ``market_category`` framed,
never naming a destination (spec §4a).

The cross-slice functions are resolved through :mod:`app.services.ports` so
this module imports cleanly even before Slices B/C land; tests monkeypatch the
ports.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.models import Batch, Payout, RoutingDecision
from app.services import ports
from app.services.routing import decide_route
from app.services.justify import justify
from app.statemachine import State, transition

log = logging.getLogger(__name__)


def _all_buyers(db: Session) -> list[Any]:
    """All buyers (the routing engine picks among them)."""
    from app.models import Buyer

    return db.query(Buyer).all()


def _latest_payout(db: Session, batch: Batch) -> Payout | None:
    """The payout ``decide_route`` just created for ``batch`` (if any)."""
    return (
        db.query(Payout)
        .filter(Payout.batch_id == batch.id)
        .order_by(Payout.id.desc())
        .first()
    )


def _premium_price_was(db: Session, batch: Batch) -> float | None:
    """Best-effort 'payout_was' — the premium price the farmer expected.

    For the MVP demo this is the batch kg × the premium contract's price/kg.
    Used only for the farmer-facing message wording; None when unknown.
    """

    shipment = batch.virtual_shipment if batch.virtual_shipment_id else None
    contract = shipment.contract if (shipment and shipment.contract_id) else None
    if contract is not None:
        return float(batch.kg) * float(contract.price_per_kg)
    return None


def run_handoff(db: Session, batch: Batch) -> RoutingDecision | None:
    """Run the full handoff re-grade + reroute cascade for one shipped batch.

    Returns the ``RoutingDecision`` row (or ``None`` on the no-decay happy
    path). Always advances the batch out of ``SHIPPED`` to a terminal/handoff
    state. Never raises on a messaging/justification failure — the audit trail
    + DB state are the source of truth.
    """

    photo = ports.get_batch_photo(batch)

    # The handoff ALWAYS re-grades (a real second grade). The decay flag only
    # controls whether the image is degraded first, so a decay-flagged batch
    # reads lower while a normal batch holds its farm grade.
    if batch.decay_on_handoff:
        photo = ports.simulate_decay(photo)

    result = ports.grade(photo, batch.crop or "tomato")
    handoff_grade = result["grade"]
    reason = result.get("reason", "")

    batch.handoff_grade = handoff_grade
    batch.grade_reason_handoff = reason
    transition(db, batch, State.GRADED_HANDOFF, handoff_grade=handoff_grade)

    contract = None
    if batch.virtual_shipment_id and batch.virtual_shipment:
        contract = batch.virtual_shipment.contract

    buyers = _all_buyers(db)
    decision = decide_route(db, batch, handoff_grade, contract, buyers)

    payout = _latest_payout(db, batch)

    # Justification text (best-effort; never blocks the cascade).
    justification: str | None = None
    if decision is not None:
        try:
            justification = justify(decision, payout, farm_grade=batch.farm_grade)
            decision.claude_justification = justification
            db.commit()
        except Exception as exc:  # noqa: BLE001 — messaging/LLM must not break flow
            log.warning("justify() failed for batch %s: %s", batch.id, exc)

    # Farmer message (best-effort; never blocks).
    if decision is not None:
        try:
            ports.send_farmer_update(
                batch.farmer.telegram_chat_id,
                {
                    "crop": batch.crop,
                    "farm_grade": batch.farm_grade,
                    "handoff_grade": handoff_grade,
                    "market_category": (
                        payout.market_category.value if payout else "market"
                    ),
                    "payout_was": _premium_price_was(db, batch),
                    "payout_now": float(payout.amount) if payout else None,
                    "reason_code": decision.reason_code.value
                    if decision.reason_code
                    else None,
                },
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("send_farmer_update() failed for batch %s: %s", batch.id, exc)

    return decision

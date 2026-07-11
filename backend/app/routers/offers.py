"""Offer routes (spec §4a).

``GET /offers`` is the secondary-buyer market view: batches that were rerouted
away from the premium buyer (status REROUTED or DELIVERED_SECONDARY). The role
gate (secondary buyer only) is real.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth import require_buyer
from app.db import get_db
from app.models import Batch, UserRole
from app.statemachine import State

router = APIRouter(prefix="/offers", tags=["offers"])


@router.get("")
def list_offers(
    _=Depends(require_buyer(UserRole.secondary_buyer)),
    db: Session = Depends(get_db),
):
    """Secondary-buyer market view (spec §4a visibility).

    Returns batches rerouted to a secondary buyer — status REROUTED or
    DELIVERED_SECONDARY — as ``{id, crop, kg, handoff_grade}`` (grade + kg +
    no contract leak).
    """
    rows = (
        db.query(Batch)
        .filter(Batch.status.in_([State.REROUTED.value, State.DELIVERED_SECONDARY.value]))
        .order_by(Batch.id)
        .all()
    )
    return [
        {
            "id": b.id,
            "crop": b.crop,
            "kg": b.kg,
            "handoff_grade": b.handoff_grade,
        }
        for b in rows
    ]

"""Pickup routes (spec §4).

``GET /pickups`` is the composter's view of batches routed to them for
composting (status COMPOSTED). The role gate (composter only) is real.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth import require_composter
from app.db import get_db
from app.models import Batch
from app.statemachine import State

router = APIRouter(prefix="/pickups", tags=["pickups"])


@router.get("")
def list_pickups(_=Depends(require_composter), db: Session = Depends(get_db)):
    """Composter's inbound batches (spec §4).

    Returns composted batches as ``{id, crop, kg}``.
    """
    rows = (
        db.query(Batch)
        .filter(Batch.status == State.COMPOSTED.value)
        .order_by(Batch.id)
        .all()
    )
    return [{"id": b.id, "crop": b.crop, "kg": b.kg} for b in rows]

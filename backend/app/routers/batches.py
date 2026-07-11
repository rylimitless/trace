"""Batch routes (spec §4).

Two routes here:

* ``GET /batches`` — admin-only list of every batch (spec §4a — admin sees
  everything).
* ``POST /batches/{id}/dispute`` — premium buyer opens a dispute on a
  DELIVERED batch they received.

Both are gated by their real role dependency so the 401/403 contract holds.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth import require_admin, require_buyer
from app.db import get_db
from app.models import Batch, UserRole
from app.statemachine import State, transition

router = APIRouter(prefix="/batches", tags=["batches"])


@router.get("")
def list_batches(_=Depends(require_admin), db: Session = Depends(get_db)):
    """Admin-only list of every batch (spec §4a — admin sees everything).

    Returns the fields the admin timeline needs; no buyer-side scoping.
    """
    rows = db.query(Batch).order_by(Batch.id).all()
    return [
        {
            "id": b.id,
            "farmer_id": b.farmer_id,
            "crop": b.crop,
            "kg": b.kg,
            "status": b.status,
            "farm_grade": b.farm_grade,
            "handoff_grade": b.handoff_grade,
            "decay_on_handoff": b.decay_on_handoff,
        }
        for b in rows
    ]


@router.post("/{batch_id}/dispute")
def dispute_batch(
    batch_id: int,
    _=Depends(require_buyer(UserRole.premium_buyer)),
    db: Session = Depends(get_db),
):
    """Premium buyer opens a dispute on a DELIVERED batch they received.

    404 if the batch does not exist; 409 if it is not DELIVERED. The
    delivered -> disputed edge has no guard, so ``transition`` always succeeds
    once the source state is correct.
    """
    batch = db.get(Batch, batch_id)
    if batch is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="batch not found")
    if batch.status != State.DELIVERED.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="only delivered batches can be disputed",
        )
    transition(db, batch, State.DISPUTED)
    return {"batch_id": batch.id, "status": State.DISPUTED.value}

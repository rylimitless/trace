"""Payout routes (spec §4).

``GET /payouts`` is admin-only and returns every payout row. The role gate
is real so the 401/403 contract holds.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth import require_admin
from app.db import get_db
from app.models import Payout

router = APIRouter(prefix="/payouts", tags=["payouts"])


@router.get("")
def list_payouts(_=Depends(require_admin), db: Session = Depends(get_db)):
    """Admin-only list of all payouts (spec §4a — admin sees everything).

    Returns ``market_category`` (the market the batch cleared), not the
    destination string.
    """
    rows = db.query(Payout).order_by(Payout.id).all()
    return [
        {
            "id": p.id,
            "farmer_id": p.farmer_id,
            "batch_id": p.batch_id,
            "market_category": p.market_category.value if p.market_category else None,
            "kg": p.kg,
            "amount": float(p.amount),
            "status": p.status.value if p.status else None,
        }
        for p in rows
    ]

"""Contract routes (spec §4a/§4b).

Three routes:

* ``GET /contracts`` — admin-only list (stub, 501).
* ``GET /contracts/mine`` — **real**: returns the contracts scoped to the
  calling premium buyer's ``buyer_id`` (spec §4a). Proves per-buyer DB scoping:
  a buyer never sees another buyer's contracts.
* ``POST /contracts/{id}/confirm`` — premium buyer confirms a contract
  (stub, 501).

``GET /contracts`` is admin-gated; ``/mine`` and ``/confirm`` are
premium-buyer-gated. The role gates are real; only ``/mine`` has a real
handler today.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import require_admin, require_buyer
from app.db import get_db
from app.models import (
    Batch,
    Contract,
    ContractStatus,
    Route,
    User,
    UserRole,
    VirtualShipmentBatch,
)
from app.services.aggregation import pool_for_contract
from app.statemachine import State, transition

router = APIRouter(prefix="/contracts", tags=["contracts"])


@router.get("")
def list_contracts(_=Depends(require_admin)):
    """Admin-only list of all contracts. Stub."""
    raise HTTPException(status_code=501, detail="Not implemented")


@router.get("/mine")
def my_contracts(
    user: User = Depends(require_buyer(UserRole.premium_buyer)),
    db: Session = Depends(get_db),
):
    """Contracts belonging to the calling premium buyer (spec §4a scoping).

    Filters strictly on ``buyer_id == current_user.buyer_id`` so a buyer can
    never see another buyer's contracts. Returns the fields the buyer UI needs:
    ``{id, crop, grade, kg_target, status}``.
    """
    rows = (
        db.query(Contract)
        .filter(Contract.buyer_id == user.buyer_id)
        .order_by(Contract.id)
        .all()
    )
    return [
        {
            "id": c.id,
            "crop": c.crop,
            "grade": c.grade,
            "kg_target": c.kg_target,
            "status": c.status.value if c.status else None,
        }
        for c in rows
    ]


@router.post("/{contract_id}/confirm")
def confirm_contract(
    contract_id: int,
    user: User = Depends(require_buyer(UserRole.premium_buyer)),
    db: Session = Depends(get_db),
):
    """Premium buyer confirms a contract (spec §4b).

    The calling premium buyer must own the contract (caller's ``buyer_id`` ==
    ``contract.buyer_id``). Confirmation pools the contract's GRADED_FARM
    batches into a VirtualShipment, transitions each batch CONTRACTED then
    SHIPPED against a freshly created returning-leg Route, and marks the
    contract ``fulfilling``.

    Returns ``{contract_id, shipped_batch_count, route_id}``.
    """
    contract = db.get(Contract, contract_id)
    if contract is None:
        raise HTTPException(status_code=404, detail="Contract not found")

    # Owns-contract check (spec §4b): only the premium buyer who owns the
    # contract may confirm it.
    if user.buyer_id != contract.buyer_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    # Pool GRADED_FARM batches matching crop+grade into a VirtualShipment.
    # pool_for_contract transitions each batch GRADED_FARM -> POOLED.
    shipment = pool_for_contract(db, contract)
    if shipment is None:
        raise HTTPException(status_code=409, detail="no batches to pool")

    # Re-load the pooled batches through the shipment's link rows (they are
    # now POOLED, ready for the CONTRACTED edge).
    batches = (
        db.execute(
            select(Batch)
            .join(VirtualShipmentBatch, VirtualShipmentBatch.batch_id == Batch.id)
            .where(VirtualShipmentBatch.shipment_id == shipment.id)
        )
        .scalars()
        .all()
    )

    # 1. Contract each batch against this contract.
    for batch in batches:
        transition(db, batch, State.CONTRACTED, contract_id=contract.id)

    # 2. Build one returning-leg Route carrying every batch.
    total_kg = sum(b.kg for b in batches)
    route = Route(
        buyer_id=contract.buyer_id,
        batch_ids=[b.id for b in batches],
        returning_leg_capacity=total_kg,
    )
    db.add(route)
    db.flush()  # populate route.id

    # 3. Ship each batch on that route.
    for batch in batches:
        batch.route_id = route.id
        transition(db, batch, State.SHIPPED, route_id=route.id)

    # 4. Mark the contract fulfilling and commit.
    contract.status = ContractStatus.fulfilling
    db.commit()

    return {
        "contract_id": contract.id,
        "shipped_batch_count": len(batches),
        "route_id": route.id,
    }

"""Telegram bot-facing endpoints.

These are called by the external Telegram bot process over HTTP (not by the
Telegram Bot API webhook). They are unauthenticated on purpose — the bot and
backend share a private network (Docker Compose) and the bot is a trusted
internal caller.

Two routes:

* ``POST /telegram/intent`` — farmer starts a new harvest: upsert Farmer by
  ``telegram_chat_id``, create a ``Batch`` at ``HARVESTED``, issue a capture
  token, and return the token + batch id so the bot can reply to the farmer.
* ``GET /telegram/batch/{batch_id_or_token}`` — look up a batch by id or
  capture-token prefix and return its status/grade for the farmer's "Track"
  flow.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from sqlalchemy import func

from app.auth import generate_capture_token
from app.db import get_db
from app.models import Batch, Buyer, Contract, ContractStatus, Farmer
from app.statemachine import State

router = APIRouter(prefix="/telegram", tags=["telegram"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class IntentRequest(BaseModel):
    telegram_chat_id: str
    crop: str
    kg: float


class IntentResponse(BaseModel):
    batch_id: int
    crop: str
    kg: float
    capture_token: str


class TrackResponse(BaseModel):
    id: int
    crop: str
    kg: float
    status: str
    farm_grade: str | None


class DemandItem(BaseModel):
    crop: str
    grade: str | None
    kg_needed: float
    source: str


class DemandResponse(BaseModel):
    items: list[DemandItem]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/demand", response_model=DemandResponse)
def demand_feed(db: Session = Depends(get_db)):
    """Anonymized demand feed — open contracts + buyer demand signals.
    No buyer names, no prices, no contract ids (spec §4a)."""
    contract_rows = (
        db.query(
            Contract.crop,
            Contract.grade,
            func.sum(Contract.kg_target).label("total_kg"),
            func.count(Contract.id).label("contracts"),
        )
        .filter(Contract.status == ContractStatus.open)
        .group_by(Contract.crop, Contract.grade)
        .order_by(Contract.crop, Contract.grade)
        .all()
    )
    buyer_rows = (
        db.query(
            Buyer.demand_crop,
            Buyer.demand_grade,
            func.sum(Buyer.demand_kg).label("total_kg"),
            func.count(Buyer.id).label("buyers"),
        )
        .filter(Buyer.demand_crop.isnot(None), Buyer.demand_kg.isnot(None))
        .group_by(Buyer.demand_crop, Buyer.demand_grade)
        .all()
    )

    items: list[dict] = []
    seen = set()
    for r in contract_rows:
        key = (r.crop, r.grade)
        seen.add(key)
        items.append(DemandItem(
            crop=r.crop,
            grade=r.grade,
            kg_needed=float(r.total_kg),
            source=f"{r.contracts} contract(s)",
        ))
    for r in buyer_rows:
        key = (r.demand_crop, r.demand_grade)
        if key not in seen:
            items.append(DemandItem(
                crop=r.demand_crop,
                grade=r.demand_grade,
                kg_needed=float(r.total_kg),
                source=f"{r.buyers} buyer(s)",
            ))

    return DemandResponse(items=items)


@router.post("/intent", response_model=IntentResponse, status_code=status.HTTP_201_CREATED)
def create_intent(body: IntentRequest, db: Session = Depends(get_db)):
    """A farmer has signalled intent to harvest. Upsert the Farmer by
    ``telegram_chat_id``, create a ``HARVESTED`` batch, issue a capture token,
    and return everything the bot needs to reply to the farmer."""
    # Upsert farmer
    farmer = (
        db.query(Farmer)
        .filter(Farmer.telegram_chat_id == body.telegram_chat_id)
        .first()
    )
    if farmer is None:
        farmer = Farmer(
            name=f"Farmer-{body.telegram_chat_id[-6:]}",
            telegram_chat_id=body.telegram_chat_id,
        )
        db.add(farmer)
        db.flush()

    batch = Batch(
        farmer_id=farmer.id,
        crop=body.crop.lower().strip(),
        kg=body.kg,
        lat=farmer.lat,
        lng=farmer.lng,
        status=State.HARVESTED.value,
        capture_token="pending",
        decay_on_handoff=False,
    )
    db.add(batch)
    db.flush()

    token = generate_capture_token(db, batch)

    return {
        "batch_id": batch.id,
        "crop": batch.crop,
        "kg": batch.kg,
        "capture_token": token,
    }


@router.get("/batch/{lookup}", response_model=TrackResponse)
def track_batch(lookup: str, db: Session = Depends(get_db)):
    """Look up a batch by numeric id or capture-token prefix."""
    batch: Batch | None = None
    try:
        batch_id = int(lookup)
        batch = db.query(Batch).filter(Batch.id == batch_id).first()
    except ValueError:
        batch = (
            db.query(Batch)
            .filter(Batch.capture_token.like(f"{lookup}%"))
            .first()
        )

    if batch is None:
        raise HTTPException(status_code=404, detail="Batch not found")

    return {
        "id": batch.id,
        "crop": batch.crop,
        "kg": batch.kg,
        "status": batch.status,
        "farm_grade": batch.farm_grade,
    }

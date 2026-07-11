"""TRACE domain models — SQLAlchemy 2.0 declarative.

All tables from spec §6 (MVP design). One file, enums at top, ORM models below
ordered so foreign-key targets are defined before they are referenced.

The same models run on Postgres (production) and SQLite (tests). Enum columns
use string-backed ``Enum`` objects (``native_enum=False``) so they are portable
across both backends.
"""

from datetime import datetime
from enum import Enum

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum as SAEnum,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


# ---------------------------------------------------------------------------
# Enums (string-backed, cross-DB safe)
# ---------------------------------------------------------------------------


class UserRole(str, Enum):
    admin = "admin"
    premium_buyer = "premium_buyer"
    secondary_buyer = "secondary_buyer"
    composter = "composter"


class BuyerType(str, Enum):
    premium = "premium"
    secondary = "secondary"
    composter = "composter"


class ContractStatus(str, Enum):
    open = "open"
    fulfilling = "fulfilling"
    fulfilled = "fulfilled"
    short = "short"


class ReasonCode(str, Enum):
    transit_decay = "transit_decay"
    route_disruption = "route_disruption"
    quality_mismatch = "quality_mismatch"


class MarketCategory(str, Enum):
    premium_market = "premium_market"
    secondary_market = "secondary_market"
    composted = "composted"


class PayoutStatus(str, Enum):
    held = "held"
    released = "released"


# Shared helper: build a string-backed Enum column from a Python enum.
def _enum_col(enum_cls, length: int = 32, **kwargs):
    return SAEnum(
        enum_cls,
        native_enum=False,
        length=length,
        validate_strings=True,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(_enum_col(UserRole), nullable=False)
    buyer_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("buyers.id"), nullable=True
    )

    buyer: Mapped["Buyer | None"] = relationship("Buyer", foreign_keys=[buyer_id])


class Farmer(Base):
    __tablename__ = "farmers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    telegram_chat_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lng: Mapped[float | None] = mapped_column(Float, nullable=True)


class Buyer(Base):
    __tablename__ = "buyers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[BuyerType] = mapped_column(_enum_col(BuyerType), nullable=False)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    demand_crop: Mapped[str | None] = mapped_column(String(128), nullable=True)
    demand_grade: Mapped[str | None] = mapped_column(String(32), nullable=True)
    demand_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_per_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    capacity: Mapped[float | None] = mapped_column(Float, nullable=True)


class Contract(Base):
    __tablename__ = "contracts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    buyer_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("buyers.id"), nullable=False
    )
    crop: Mapped[str] = mapped_column(String(128), nullable=False)
    grade: Mapped[str] = mapped_column(String(32), nullable=False)
    kg_target: Mapped[float] = mapped_column(Float, nullable=False)
    price_per_kg: Mapped[float] = mapped_column(Float, nullable=False)
    deadline: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[ContractStatus] = mapped_column(
        _enum_col(ContractStatus), nullable=False
    )

    buyer: Mapped["Buyer"] = relationship("Buyer", foreign_keys=[buyer_id])


class Batch(Base):
    """The atom of TRACE: created at intent, graded twice, ends at one destination."""

    __tablename__ = "batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    farmer_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("farmers.id"), nullable=False
    )
    crop: Mapped[str] = mapped_column(String(128), nullable=False)
    kg: Mapped[float] = mapped_column(Float, nullable=False)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    # status is a str set ONLY by the state machine (§7) — never assigned directly.
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    farm_grade: Mapped[str | None] = mapped_column(String(32), nullable=True)
    handoff_grade: Mapped[str | None] = mapped_column(String(32), nullable=True)
    final_grade: Mapped[str | None] = mapped_column(String(32), nullable=True)
    decay_event: Mapped[str | None] = mapped_column(String(64), nullable=True)
    photo_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)
    grade_reason_farm: Mapped[str | None] = mapped_column(Text, nullable=True)
    grade_reason_handoff: Mapped[str | None] = mapped_column(Text, nullable=True)
    capture_token: Mapped[str] = mapped_column(
        String(128), unique=True, nullable=False
    )
    capture_token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    virtual_shipment_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("virtual_shipments.id"), nullable=True
    )
    route_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("routes.id"), nullable=True
    )
    # Approved deviation: added in Task 2 so Task 6 needs no migration.
    decay_on_handoff: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )

    farmer: Mapped["Farmer"] = relationship("Farmer", foreign_keys=[farmer_id])
    virtual_shipment: Mapped["VirtualShipment | None"] = relationship(
        "VirtualShipment", foreign_keys=[virtual_shipment_id]
    )
    route: Mapped["Route | None"] = relationship("Route", foreign_keys=[route_id])


class VirtualShipment(Base):
    __tablename__ = "virtual_shipments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    contract_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("contracts.id"), nullable=False
    )
    total_kg: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)

    contract: Mapped["Contract"] = relationship("Contract", foreign_keys=[contract_id])


class VirtualShipmentBatch(Base):
    """Link table holding each batch's % contribution to a shipment."""

    __tablename__ = "virtual_shipment_batches"

    shipment_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("virtual_shipments.id"), primary_key=True
    )
    batch_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("batches.id"), primary_key=True
    )
    pct_contribution: Mapped[float] = mapped_column(Float, nullable=False)

    shipment: Mapped["VirtualShipment"] = relationship(
        "VirtualShipment", foreign_keys=[shipment_id]
    )
    batch: Mapped["Batch"] = relationship("Batch", foreign_keys=[batch_id])


class Route(Base):
    __tablename__ = "routes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    buyer_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("buyers.id"), nullable=False
    )
    pickup_geo: Mapped[str | None] = mapped_column(String(255), nullable=True)
    returning_leg_capacity: Mapped[float | None] = mapped_column(
        Float, nullable=True
    )
    batch_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # Demo-only flag: when True, this route's primary leg is closed (washout)
    # and the routing engine must recompute to a fallback destination. Spec §13
    # anomaly 2 (route disruption). Default False.
    washed_out: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    buyer: Mapped["Buyer"] = relationship("Buyer", foreign_keys=[buyer_id])


class RoutingDecision(Base):
    """Every reroute is a recorded, justified event (spec §6)."""

    __tablename__ = "routing_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("batches.id"), nullable=False
    )
    from_destination: Mapped[str | None] = mapped_column(String(128), nullable=True)
    to_destination: Mapped[str | None] = mapped_column(String(128), nullable=True)
    reason_code: Mapped[ReasonCode] = mapped_column(_enum_col(ReasonCode), nullable=False)
    claude_justification: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )

    batch: Mapped["Batch"] = relationship("Batch", foreign_keys=[batch_id])


class Payout(Base):
    __tablename__ = "payouts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    farmer_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("farmers.id"), nullable=False
    )
    batch_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("batches.id"), nullable=False
    )
    grade_paid_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    destination: Mapped[str | None] = mapped_column(String(128), nullable=True)
    market_category: Mapped[MarketCategory] = mapped_column(
        _enum_col(MarketCategory), nullable=False
    )
    kg: Mapped[float] = mapped_column(Float, nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    status: Mapped[PayoutStatus] = mapped_column(
        _enum_col(PayoutStatus), nullable=False
    )

    farmer: Mapped["Farmer"] = relationship("Farmer", foreign_keys=[farmer_id])
    batch: Mapped["Batch"] = relationship("Batch", foreign_keys=[batch_id])


class AuditEvent(Base):
    """Append-only log rendered as the admin 'provenance timeline'."""

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("batches.id"), nullable=True
    )
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )

    batch: Mapped["Batch | None"] = relationship("Batch", foreign_keys=[batch_id])

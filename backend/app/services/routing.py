"""Routing decisions + payout math (spec §10, §11).

This module owns BOTH the routing rules and the payout computation. They live
in one file because :func:`decide_route` calls :func:`compute_payout` after it
resolves a destination — splitting them across modules would create a circular
dependency and obscure the single decision flow.

The entrypoint is :func:`decide_route`, invoked once a batch has reached
``GRADED_HANDOFF``. It inspects (in spec order):

  a) **Route disruption** — if the batch's ``Route.washed_out`` flag is set,
     the primary leg is closed; the batch is sent to a fallback composter
     (reason ``route_disruption``).
  b) **No decay** — handoff grade equals farm grade; deliver to the contracted
     premium buyer. No reroute decision row is written.
  c) **Downgrade A -> B** — transit decay; pull the batch from its premium
     shipment and reroute to a reachable secondary buyer. If none is found the
     batch goes to a reachable composter, else ``LOST``.
  d) **WASTE** — compost or lose the batch.

All batch status changes go through :func:`app.statemachine.transition`; this
module never assigns ``batch.status`` directly. Visibility rule (spec §4a) is
enforced in :func:`compute_payout`: ``market_category`` derives from the
destination buyer's *type*, never from a buyer name leaked to the farmer.
"""

from __future__ import annotations

import math
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy.orm import Session

from app.models import (
    Batch,
    Buyer,
    BuyerType,
    Contract,
    ContractStatus,
    MarketCategory,
    Payout,
    PayoutStatus,
    ReasonCode,
    Route,
    RoutingDecision,
)
from app.statemachine import State, transition


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Max haversine km between the route's buyer and a candidate for the candidate
#: to count as "reachable on the returning leg" (spec §10 — kept simple).
REACHABLE_KM_THRESHOLD = 50.0


# ---------------------------------------------------------------------------
# Reachability helper
# ---------------------------------------------------------------------------


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in km between two lat/lng points."""
    r = 6371.0  # Earth radius in km
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lng2 - lng1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(dlam / 2) ** 2
    )
    return 2 * r * math.asin(math.sqrt(a))


def _reachable_on_returning(route: Route | None, buyer_lat: float | None, buyer_lng: float | None) -> bool:
    """True if the candidate buyer lies within the returning-leg threshold.

    Uses the route's own buyer location as the anchor and a simple haversine
    distance. If the route (or either coordinate) is unknown, no constraint is
    applied (returns ``True``) — the MVP treats missing geo as "no constraint".
    """
    if route is None or route.buyer is None:
        return True
    anchor_lat = route.buyer.lat
    anchor_lng = route.buyer.lng
    if anchor_lat is None or anchor_lng is None or buyer_lat is None or buyer_lng is None:
        return True
    return _haversine_km(anchor_lat, anchor_lng, buyer_lat, buyer_lng) <= REACHABLE_KM_THRESHOLD


# ---------------------------------------------------------------------------
# Payout
# ---------------------------------------------------------------------------


def compute_payout(
    db: Session,
    batch: Batch,
    destination_buyer: Buyer,
    price_per_kg: float,
) -> Payout:
    """Create + commit a ``Payout`` row for ``batch`` delivered to a buyer.

    * ``amount`` = ``kg * price_per_kg`` quantized to 0.01 — EXCEPT for a
      composter destination, which is an explicit zero-amount payout (spec §11).
    * ``market_category`` derives from the buyer's *type* (visibility rule §4a):
      premium -> premium_market, secondary -> secondary_market, composter ->
      composted. The buyer NAME never leaves the internal ``destination`` field.
    * ``status``: released for the delivered/composted terminals, else held.
    """
    is_composter = destination_buyer.type == BuyerType.composter

    if is_composter:
        amount = Decimal("0.00")
    else:
        amount = (Decimal(str(batch.kg)) * Decimal(str(price_per_kg))).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

    type_to_category = {
        BuyerType.premium: MarketCategory.premium_market,
        BuyerType.secondary: MarketCategory.secondary_market,
        BuyerType.composter: MarketCategory.composted,
    }
    market_category = type_to_category[destination_buyer.type]

    grade_paid_at = batch.handoff_grade or batch.farm_grade

    # Released for terminal delivered states (compost is terminal-and-released),
    # else held until the later release step.
    released_states = {
        State.DELIVERED.value,
        State.DELIVERED_SECONDARY.value,
        State.COMPOSTED.value,
    }
    status = (
        PayoutStatus.released
        if (is_composter or batch.status in released_states)
        else PayoutStatus.held
    )

    payout = Payout(
        farmer_id=batch.farmer_id,
        batch_id=batch.id,
        grade_paid_at=grade_paid_at,
        destination=destination_buyer.name,
        market_category=market_category,
        kg=batch.kg,
        amount=amount,
        status=status,
    )
    db.add(payout)
    db.commit()
    db.refresh(payout)
    return payout


# ---------------------------------------------------------------------------
# Contract fulfillment helper
# ---------------------------------------------------------------------------


def recompute_contract_fulfillment(db: Session, contract: Contract) -> Contract:
    """Re-tally ``contract.status`` against delivered Grade-A kg.

    Sums the kg of batches still attached to the contract's shipments that have
    reached a delivered state, and flips the contract status:
      * met or exceeded ``kg_target`` -> ``fulfilled``
      * some delivered but short       -> ``short``
      * none delivered yet (in flight) -> ``fulfilling``

    Called when a batch is pulled from a shipment (A->B reroute) so the contract
    reflects the lost contribution. Kept intentionally simple for the MVP.
    """
    # Gather all batches still linked to this contract via any of its shipments.
    from app.models import VirtualShipment

    shipment_ids = [
        s.id
        for s in db.query(VirtualShipment)
        .filter(VirtualShipment.contract_id == contract.id)
        .all()
    ]
    delivered_kg = 0.0
    in_flight_kg = 0.0
    if shipment_ids:
        rows = (
            db.query(Batch)
            .filter(Batch.virtual_shipment_id.in_(shipment_ids))
            .all()
        )
        delivered_states = {
            State.DELIVERED.value,
            State.PAID.value,
        }
        for b in rows:
            if b.status in delivered_states and (b.farm_grade == "A" or b.handoff_grade == "A"):
                delivered_kg += b.kg
            elif b.status not in (
                State.LOST.value,
                State.COMPOSTED.value,
                State.DELIVERED_SECONDARY.value,
            ):
                in_flight_kg += b.kg

    if delivered_kg >= contract.kg_target:
        contract.status = ContractStatus.fulfilled
    elif delivered_kg > 0 or in_flight_kg > 0:
        contract.status = ContractStatus.short
    else:
        contract.status = ContractStatus.open
    db.commit()
    return contract


# ---------------------------------------------------------------------------
# decide_route
# ---------------------------------------------------------------------------


def decide_route(
    db: Session,
    batch: Batch,
    handoff_grade: str,
    contract: Contract | None,
    buyers: list[Buyer],
) -> RoutingDecision | None:
    """Resolve a ``GRADED_HANDOFF`` batch to a destination + payout (spec §10).

    Returns the ``RoutingDecision`` row if a reroute/disruption decision was
    written, or ``None`` for the no-decay happy path (where no decision row is
    stored — the batch simply advances to ``DELIVERED``).
    """
    route = batch.route

    # ------------------------------------------------------------------ #
    # (a) Route disruption — checked FIRST (spec §10).
    # ------------------------------------------------------------------ #
    if route is not None and route.washed_out:
        fallback = _pick_fallback_composter(db, batch, buyers, route, waste=(handoff_grade == "WASTE"))
        if fallback is None:
            # No fallback reachable at all — the batch is lost.
            transition(db, batch, State.LOST)
            db.commit()
            return _record_decision(
                db,
                batch,
                from_destination=_route_buyer_name(route),
                to_destination=None,
                reason_code=ReasonCode.route_disruption,
            )
        # Move to the terminal state. The state machine only allows
        # REROUTED -> DELIVERED_SECONDARY (not REROUTED -> COMPOSTED), so a
        # composter fallback goes directly GRADED_HANDOFF -> COMPOSTED; any
        # other fallback buyer goes through the REROUTED intermediate.
        if fallback.type == BuyerType.composter:
            transition(db, batch, State.COMPOSTED)
        else:
            transition(db, batch, State.REROUTED)
            transition(db, batch, State.DELIVERED_SECONDARY)
        _assign_route_for_buyer(db, batch, fallback)
        compute_payout(db, batch, fallback, price_per_kg=_price(fallback))
        db.commit()
        return _record_decision(
            db,
            batch,
            from_destination=_route_buyer_name(route),
            to_destination=fallback.name,
            reason_code=ReasonCode.route_disruption,
        )

    # ------------------------------------------------------------------ #
    # (b) No decay — handoff grade equals farm grade.
    # ------------------------------------------------------------------ #
    if handoff_grade == batch.farm_grade:
        transition(db, batch, State.DELIVERED)
        db.commit()
        return None  # happy path: no reroute decision row

    # ------------------------------------------------------------------ #
    # (d) WASTE — handled before the A->B downgrade since WASTE is its own
    # grade, not a B downgrade. (handoff_grade != farm_grade already true.)
    # ------------------------------------------------------------------ #
    if handoff_grade == "WASTE":
        composter = _pick_composter(db, buyers, batch.kg, route)
        if composter is not None:
            transition(db, batch, State.COMPOSTED)
            _assign_route_for_buyer(db, batch, composter)
            compute_payout(db, batch, composter, price_per_kg=_price(composter))
            db.commit()
            return _record_decision(
                db,
                batch,
                from_destination=_route_buyer_name(route),
                to_destination=composter.name,
                reason_code=ReasonCode.transit_decay,
            )
        # No reachable composter -> lost.
        transition(db, batch, State.LOST)
        db.commit()
        return _record_decision(
            db,
            batch,
            from_destination=_route_buyer_name(route),
            to_destination=None,
            reason_code=ReasonCode.transit_decay,
        )

    # ------------------------------------------------------------------ #
    # (c) Downgrade A -> B (or any non-WASTE grade drop).
    # ------------------------------------------------------------------ #
    secondary = _pick_secondary(db, batch, buyers, route)
    if secondary is not None:
        # Pull the batch off its premium shipment.
        batch.virtual_shipment_id = None
        transition(db, batch, State.REROUTED)
        transition(db, batch, State.DELIVERED_SECONDARY)
        _assign_route_for_buyer(db, batch, secondary)
        compute_payout(db, batch, secondary, price_per_kg=_price(secondary))
        if contract is not None:
            recompute_contract_fulfillment(db, contract)
        db.commit()
        return _record_decision(
            db,
            batch,
            from_destination=_route_buyer_name(route),
            to_destination=secondary.name,
            reason_code=ReasonCode.transit_decay,
        )

    # No secondary reachable — try a composter before declaring LOST.
    composter = _pick_composter(db, buyers, batch.kg, route)
    if composter is not None:
        batch.virtual_shipment_id = None
        transition(db, batch, State.COMPOSTED)
        _assign_route_for_buyer(db, batch, composter)
        compute_payout(db, batch, composter, price_per_kg=_price(composter))
        if contract is not None:
            recompute_contract_fulfillment(db, contract)
        db.commit()
        return _record_decision(
            db,
            batch,
            from_destination=_route_buyer_name(route),
            to_destination=composter.name,
            reason_code=ReasonCode.transit_decay,
        )

    # Nothing reachable — the batch is lost.
    batch.virtual_shipment_id = None
    transition(db, batch, State.LOST)
    if contract is not None:
        recompute_contract_fulfillment(db, contract)
    db.commit()
    return _record_decision(
        db,
        batch,
        from_destination=_route_buyer_name(route),
        to_destination=None,
        reason_code=ReasonCode.transit_decay,
    )


# ---------------------------------------------------------------------------
# Buyer-selection helpers
# ---------------------------------------------------------------------------


def _pick_secondary(
    db: Session,
    batch: Batch,
    buyers: list[Buyer],
    route: Route | None,
) -> Buyer | None:
    """First reachable secondary buyer matching crop with remaining capacity."""
    candidates = [
        b
        for b in buyers
        if b.type == BuyerType.secondary
        and b.demand_crop == batch.crop
        and _has_capacity(b, batch.kg)
        and _reachable_on_returning(route, b.lat, b.lng)
    ]
    return candidates[0] if candidates else None


def _pick_composter(
    db: Session,
    buyers: list[Buyer],
    kg: float,
    route: Route | None,
) -> Buyer | None:
    """First reachable composter with remaining capacity for ``kg``."""
    candidates = [
        b
        for b in buyers
        if b.type == BuyerType.composter
        and _has_capacity(b, kg)
        and _reachable_on_returning(route, b.lat, b.lng)
    ]
    return candidates[0] if candidates else None


def _pick_fallback_composter(
    db: Session,
    batch: Batch,
    buyers: list[Buyer],
    route: Route | None,
    *,
    waste: bool,
) -> Buyer | None:
    """Pick a fallback destination for the route-disruption branch.

    A composter is the expected fallback. If the batch is waste, prefer a
    composter with capacity; otherwise still prefer a composter. Falls back to
    any reachable composter regardless of capacity before giving up.
    """
    with_cap = [
        b
        for b in buyers
        if b.type == BuyerType.composter
        and _reachable_on_returning(route, b.lat, b.lng)
        and _has_capacity(b, batch.kg)
    ]
    if with_cap:
        return with_cap[0]
    # Any reachable composter (capacity may be tight but the route is gone).
    any_composter = [
        b
        for b in buyers
        if b.type == BuyerType.composter
        and _reachable_on_returning(route, b.lat, b.lng)
    ]
    return any_composter[0] if any_composter else None


def _has_capacity(buyer: Buyer, kg: float) -> bool:
    """True if the buyer has capacity to accept ``kg`` (None capacity = open)."""
    if buyer.capacity is None:
        return True
    return buyer.capacity >= kg


def _price(buyer: Buyer) -> float:
    """Best-effort price per kg for the buyer; 0.0 if unset (e.g. composter)."""
    return buyer.price_per_kg if buyer.price_per_kg is not None else 0.0


# ---------------------------------------------------------------------------
# Route + decision row helpers
# ---------------------------------------------------------------------------


def _route_buyer_name(route: Route | None) -> str | None:
    """The name of the buyer the route was originally heading to, or None."""
    if route is None or route.buyer is None:
        return None
    return route.buyer.name


def _assign_route_for_buyer(db: Session, batch: Batch, buyer: Buyer) -> None:
    """Attach the batch to a Route for ``buyer``: reuse an existing one or create."""
    route = db.query(Route).filter(Route.buyer_id == buyer.id).first()
    if route is None:
        route = Route(
            buyer_id=buyer.id,
            pickup_geo=f"{batch.lat},{batch.lng}" if batch.lat is not None else None,
            returning_leg_capacity=buyer.capacity,
            batch_ids=[batch.id],
            washed_out=False,
        )
        db.add(route)
        db.flush()
    else:
        ids = list(route.batch_ids or [])
        if batch.id not in ids:
            ids.append(batch.id)
        route.batch_ids = ids
    batch.route_id = route.id


def _record_decision(
    db: Session,
    batch: Batch,
    *,
    from_destination: str | None,
    to_destination: str | None,
    reason_code: ReasonCode,
    justification: str | None = None,
) -> RoutingDecision:
    """Persist a ``RoutingDecision`` row and return it."""
    decision = RoutingDecision(
        batch_id=batch.id,
        from_destination=from_destination,
        to_destination=to_destination,
        reason_code=reason_code,
        claude_justification=justification,
    )
    db.add(decision)
    db.commit()
    db.refresh(decision)
    return decision

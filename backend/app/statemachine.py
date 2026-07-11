"""Batch lifecycle state machine — the single source of truth (spec §7).

Every mutation of ``Batch.status`` MUST go through :func:`transition`. Direct
assignment to ``batch.status`` is forbidden outside this module (and the schema
smoke test). The :class:`State` enum here is the authority; ``Batch.status`` is a
plain ``String(32)`` column that merely stores ``State.value``.

Transitions are data-driven: ``TRANSITIONS`` maps ``(source, destination)`` to a
guard callable. Guards receive the keyword context (``**ctx``) and raise on a
missing precondition; returning ``None`` means "unconditional given correct
source". Task 4 wires the audit/SSE hook into :func:`transition` — the single
obvious call site is marked below.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.models import Batch


# ---------------------------------------------------------------------------
# States (spec §7) — 13 terminal + intermediate lifecycle positions.
# ---------------------------------------------------------------------------


class State(str, Enum):
    """All reachable batch states. String values are what's stored on the row."""

    HARVESTED = "HARVESTED"
    GRADED_FARM = "GRADED_FARM"
    POOLED = "POOLED"
    CONTRACTED = "CONTRACTED"
    SHIPPED = "SHIPPED"
    GRADED_HANDOFF = "GRADED_HANDOFF"
    REROUTED = "REROUTED"
    DELIVERED = "DELIVERED"
    DELIVERED_SECONDARY = "DELIVERED_SECONDARY"
    COMPOSTED = "COMPOSTED"
    PAID = "PAID"
    DISPUTED = "DISPUTED"
    LOST = "LOST"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class IllegalTransition(Exception):
    """Raised when ``(current_status, destination)`` is not a legal edge.

    Also raised by a guard when a required context key is missing — the guard
    message identifies the missing precondition.
    """

    def __init__(self, current: str, dest: str, reason: str = "not a legal edge"):
        self.current = current
        self.dest = dest
        self.reason = reason
        super().__init__(f"{current} -> {dest}: {reason}")


class MissingContext(Exception):
    """Raised by a guard when a required ctx key is absent."""


# ---------------------------------------------------------------------------
# Guards — return None on success, raise on missing precondition.
# ---------------------------------------------------------------------------

# A guard is a no-arg (just **ctx) callable; it either returns None or raises.
Guard = Callable[..., None]


def _require(ctx: dict[str, Any], key: str) -> None:
    if key not in ctx or ctx[key] is None:
        raise MissingContext(f"guard requires ctx['{key}']")


def _guard_farm_grade(**ctx: Any) -> None:
    _require(ctx, "farm_grade")


def _guard_contract_id(**ctx: Any) -> None:
    _require(ctx, "contract_id")


def _guard_route_id(**ctx: Any) -> None:
    _require(ctx, "route_id")


def _guard_handoff_grade(**ctx: Any) -> None:
    _require(ctx, "handoff_grade")


def _noop(**_ctx: Any) -> None:
    """Unconditional guard: legal given the correct source state."""
    return None


# ---------------------------------------------------------------------------
# Transition table (spec §7). Keys are ``(State, State)``.
# ---------------------------------------------------------------------------

TRANSITIONS: dict[tuple[State, State], Guard] = {
    # Happy path
    (State.HARVESTED, State.GRADED_FARM): _guard_farm_grade,
    (State.GRADED_FARM, State.POOLED): _noop,
    (State.POOLED, State.CONTRACTED): _guard_contract_id,
    (State.CONTRACTED, State.SHIPPED): _guard_route_id,
    (State.SHIPPED, State.GRADED_HANDOFF): _guard_handoff_grade,

    # Handoff outcomes
    (State.GRADED_HANDOFF, State.DELIVERED): _noop,      # grade unchanged
    (State.GRADED_HANDOFF, State.REROUTED): _noop,       # downgraded
    (State.GRADED_HANDOFF, State.COMPOSTED): _noop,      # true waste

    # Reroute resolution
    (State.REROUTED, State.DELIVERED_SECONDARY): _noop,

    # Payment terminals
    (State.DELIVERED, State.PAID): _noop,
    (State.DELIVERED_SECONDARY, State.PAID): _noop,
    (State.COMPOSTED, State.PAID): _noop,

    # Dispute can be opened from a delivered batch
    (State.DELIVERED, State.DISPUTED): _noop,

    # -> LOST: modeled explicitly from every spec-listed source.
    (State.HARVESTED, State.LOST): _noop,
    (State.GRADED_FARM, State.LOST): _noop,
    (State.POOLED, State.LOST): _noop,
    (State.CONTRACTED, State.LOST): _noop,
    (State.SHIPPED, State.LOST): _noop,
    (State.GRADED_HANDOFF, State.LOST): _noop,
    (State.REROUTED, State.LOST): _noop,
}


# ---------------------------------------------------------------------------
# Transition entrypoint
# ---------------------------------------------------------------------------


def transition(
    db: Session,
    batch: Batch,
    dest: State,
    **ctx: Any,
) -> Batch:
    """Move ``batch`` to ``dest`` if the edge is legal and its guard passes.

    Steps:
      1. Resolve the source from ``batch.status``.
      2. Look up ``(source, dest)`` in :data:`TRANSITIONS`. Absent =>
         :class:`IllegalTransition`.
      3. Run the guard with ``ctx``. A guard raises
         :class:`MissingContext` (re-raised as :class:`IllegalTransition`
         with the precondition as the reason) to signal a failed precondition.
      4. Persist ``batch.status = dest.value`` and commit.

    Task 4 will add the audit-event + SSE fan-out hook at the marked call site
    below; this function currently only mutates status and commits.

    Returns the mutated ``batch`` for convenience.
    """
    source = State(batch.status)  # raises ValueError if column was corrupted
    edge = (source, dest)
    guard = TRANSITIONS.get(edge)
    if guard is None:
        raise IllegalTransition(source.value, dest.value)

    try:
        guard(**ctx)
    except MissingContext as exc:
        # A missing precondition makes the edge illegal for this call.
        raise IllegalTransition(source.value, dest.value, reason=str(exc)) from exc

    batch.status = dest.value

    # Task 4: log_audit(db, batch, dest, ctx) + SSE fan-out go here.
    # (Do NOT implement here — Task 4 owns the audit hook.)

    db.commit()
    return batch

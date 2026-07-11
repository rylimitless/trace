"""Table-driven tests for the batch state machine (spec §7).

Two things are exercised:

1. **Every legal edge** in ``TRANSITIONS`` advances ``batch.status`` to the
   destination, using the guard-required context for that edge (looked up from
   ``GUARD_CTX``). This is generated from the table itself, so adding an edge to
   the spec only requires adding a row to ``TRANSITIONS`` and (if guarded) a
   ctx entry here.

2. **A sample of illegal edges** raise :class:`IllegalTransition` — a
   representative cross-section including a forward skip (HARVESTED -> PAID), a
   backward move (POOLED -> HARVESTED), a self-loop (HARVESTED -> HARVESTED),
   and a transition out of a terminal state (PAID -> DELIVERED).

Guard precondition failures (missing ctx key) are asserted separately to confirm
they surface as ``IllegalTransition``.
"""

from __future__ import annotations

import uuid

import pytest

from app.models import Batch, Farmer
from app.statemachine import (
    IllegalTransition,
    State,
    TRANSITIONS,
    transition,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_batch(db_session, *, status: State = State.HARVESTED) -> Batch:
    """Insert a Farmer + a fresh Batch in ``status``; commit and return batch."""
    farmer = Farmer(name=f"farmer-{uuid.uuid4().hex[:8]}", lat=1.30, lng=103.85)
    db_session.add(farmer)
    db_session.flush()
    batch = Batch(
        farmer_id=farmer.id,
        crop="tomato",
        kg=20.0,
        status=status.value,
        capture_token=f"tok-{uuid.uuid4().hex}",
    )
    db_session.add(batch)
    db_session.commit()
    return batch


# ctx that satisfies each guard. Keys mirror the guard requirements.
GUARD_CTX: dict[State, dict] = {
    State.GRADED_FARM: {"farm_grade": "A"},
    State.CONTRACTED: {"contract_id": 42},
    State.SHIPPED: {"route_id": 7},
    State.GRADED_HANDOFF: {"handoff_grade": "A"},
}


def _ctx_for(dest: State) -> dict:
    """Return the ctx required by whichever guard guards the edge into dest."""
    return GUARD_CTX.get(dest, {}).copy()


# ---------------------------------------------------------------------------
# Legal transitions: iterate the TRANSITIONS table.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "source, dest",
    list(TRANSITIONS.keys()),
    ids=[f"{s.name}->{d.name}" for s, d in TRANSITIONS.keys()],
)
def test_legal_transition_advances(db_session, source: State, dest: State):
    """Every edge in TRANSITIONS moves the batch from source to dest."""
    batch = _make_batch(db_session, status=source)
    ctx = _ctx_for(dest)
    transition(db_session, batch, dest, **ctx)
    assert batch.status == dest.value
    # And it's actually persisted — re-read from the identity map is enough
    # because commit flushes; verify the column holds the string value.
    db_session.refresh(batch)
    assert batch.status == dest.value


def test_all_thirteen_states_present():
    """The State enum defines exactly the 13 spec states, by name and value."""
    expected = {
        "HARVESTED", "GRADED_FARM", "POOLED", "CONTRACTED", "SHIPPED",
        "GRADED_HANDOFF", "REROUTED", "DELIVERED", "DELIVERED_SECONDARY",
        "COMPOSTED", "PAID", "DISPUTED", "LOST",
    }
    assert {s.name for s in State} == expected
    # str(Enum) values equal the uppercase names (stored on the column).
    assert all(s.value == s.name for s in State)


def test_transition_count_matches_spec():
    """Sanity: the number of legal edges is what we expect (see spec §7)."""
    # 5 happy-path + 3 handoff outcomes + 1 reroute resolution +
    # 3 payment terminals + 1 dispute + 7 -> LOST = 20.
    assert len(TRANSITIONS) == 20


# ---------------------------------------------------------------------------
# Guards: missing ctx -> IllegalTransition (not a bare MissingContext).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "source, dest, missing_key",
    [
        (State.HARVESTED, State.GRADED_FARM, "farm_grade"),
        (State.POOLED, State.CONTRACTED, "contract_id"),
        (State.CONTRACTED, State.SHIPPED, "route_id"),
        (State.SHIPPED, State.GRADED_HANDOFF, "handoff_grade"),
    ],
    ids=["farm_grade", "contract_id", "route_id", "handoff_grade"],
)
def test_guard_missing_ctx_raises_illegal(
    db_session, source: State, dest: State, missing_key: str
):
    """A guarded edge with its required key absent raises IllegalTransition."""
    batch = _make_batch(db_session, status=source)
    with pytest.raises(IllegalTransition):
        transition(db_session, batch, dest)  # ctx deliberately lacks missing_key
    # Status must be unchanged on a failed transition.
    db_session.refresh(batch)
    assert batch.status == source.value


# ---------------------------------------------------------------------------
# Illegal transitions: a representative sample.
# ---------------------------------------------------------------------------


ILLEGAL_EDGES = [
    (State.HARVESTED, State.PAID, "forward skip past entire lifecycle"),
    (State.HARVESTED, State.POOLED, "skip the farm grade"),
    (State.POOLED, State.HARVESTED, "backward"),
    (State.HARVESTED, State.HARVESTED, "self-loop"),
    (State.PAID, State.DELIVERED, "out of a terminal"),
    (State.PAID, State.LOST, "out of a terminal (paid cannot be lost)"),
    (State.COMPOSTED, State.DELIVERED, "out of a terminal"),
    (State.DISPUTED, State.PAID, "disputed is terminal-ish; no edge"),
    (State.LOST, State.PAID, "lost is terminal"),
    (State.GRADED_FARM, State.GRADED_HANDOFF, "skip contract/ship/handoff"),
]


@pytest.mark.parametrize(
    "source, dest, reason",
    ILLEGAL_EDGES,
    ids=[f"{s.name}->{d.name}" for s, d, _ in ILLEGAL_EDGES],
)
def test_illegal_transition_raises(db_session, source: State, dest: State, reason: str):
    """Each sampled illegal edge raises IllegalTransition and leaves status."""
    batch = _make_batch(db_session, status=source)
    with pytest.raises(IllegalTransition):
        transition(db_session, batch, dest, **_ctx_for(dest))
    db_session.refresh(batch)
    assert batch.status == source.value, reason


# ---------------------------------------------------------------------------
# -> LOST from every spec-listed source.
# ---------------------------------------------------------------------------


LOST_SOURCES = [
    State.HARVESTED,
    State.GRADED_FARM,
    State.POOLED,
    State.CONTRACTED,
    State.SHIPPED,
    State.GRADED_HANDOFF,
    State.REROUTED,
]


@pytest.mark.parametrize("source", LOST_SOURCES, ids=[s.name for s in LOST_SOURCES])
def test_lost_from_each_source(db_session, source: State):
    """Spec §7: a batch can be marked LOST from each of these states."""
    batch = _make_batch(db_session, status=source)
    transition(db_session, batch, State.LOST)
    assert batch.status == State.LOST.value


def test_lost_is_terminal(db_session):
    """Once LOST, no outgoing edge exists."""
    batch = _make_batch(db_session, status=State.LOST)
    for dest in State:
        if dest is State.LOST:
            continue
        with pytest.raises(IllegalTransition):
            transition(db_session, batch, dest)

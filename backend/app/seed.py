"""Deterministic demo scenario seed (spec §11 "Definition of Done").

Produces a fixed, reproducible dataset so the demo and the test suite see the
same rows every run:

* 4 Users (admin + 3 buyer-types, bcrypt-hashed, all password ``demo1234``)
* 6 Farmers around Kingston, JA (18.0N, -76.8E), each a tomato grower with a
  distinct ``telegram_chat_id``
* 4 Buyers: 1 resort (premium), 1 school feeding (secondary), and 2 composters
  (a primary + a fallback that enables the route-disruption anomaly)
* 1 Contract (resort / tomato / grade A / 200kg / $4.00/kg / open)
* 1 Batch parked at ``GRADED_FARM`` (30kg grade-A tomato, ``decay_on_handoff``
  set True so the demo's "one batch pre-set to decay" is ready to flow)

Buyer-type Users are linked to their ``Buyer`` rows via ``buyer_id``.

Structure
---------
``run_seed(db)`` performs the inserts against any SQLAlchemy ``Session`` — it
is what tests call directly against the in-memory SQLite engine. ``main()``
parses ``--reset`` (``drop_all`` + ``create_all``), builds a session on the
configured engine, and delegates to :func:`run_seed`. Run as:

    uv run python -m app.seed --reset
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.auth import generate_capture_token, hash_password
from app.db import Base, SessionLocal, engine
from app.models import (
    Batch,
    Buyer,
    BuyerType,
    Contract,
    ContractStatus,
    Farmer,
    User,
    UserRole,
)
from app.statemachine import State

# Deterministic identifiers — tests assert against these, so do NOT change
# without updating ``tests/test_seed.py``.
DEMO_PASSWORD = "demo1234"

# Fixed deadline far in the future so the contract is always "open" regardless
# of when the seed runs (a past deadline would be misleading but not invalid;
# a generous fixed offset keeps the demo stable and the assert simple).
DEADLINE = datetime.now(timezone.utc) + timedelta(days=7)


def run_seed(db: Session) -> None:
    """Insert the deterministic demo dataset into ``db``.

    Pure inserts: no engine/schema work, no argparse. Tests call this against
    an in-memory SQLite session; :func:`main` calls it after optionally
    resetting the schema. Rows use fixed identifiers (emails, telegram chat
    ids, lat/lng) so assertions are stable across runs.
    """
    pw = hash_password(DEMO_PASSWORD)

    # ------------------------------------------------------------------ #
    # Buyers (4): created first so buyer-type Users can FK to them.
    # ------------------------------------------------------------------ #
    resort = Buyer(
        name="SeaBreeze Resort",
        type=BuyerType.premium,
        lat=18.01,
        lng=-76.82,
        demand_crop="tomato",
        demand_grade="A",
        demand_kg=200.0,
        price_per_kg=4.0,
        capacity=500.0,
    )
    school = Buyer(
        name="Kingston School Feeding Programme",
        type=BuyerType.secondary,
        lat=17.97,
        lng=-76.79,
        demand_crop="tomato",
        demand_grade="B",
        demand_kg=150.0,
        price_per_kg=2.0,
        capacity=400.0,
    )
    composter_primary = Buyer(
        name="Hillside Compost Co",
        type=BuyerType.composter,
        lat=18.05,
        lng=-76.80,
        demand_crop="tomato",
        demand_grade=None,
        demand_kg=100.0,
        price_per_kg=0.5,
        capacity=300.0,
    )
    composter_fallback = Buyer(
        name="GreenCycle Compost (fallback)",
        type=BuyerType.composter,
        lat=18.03,
        lng=-76.78,
        demand_crop="tomato",
        demand_grade=None,
        demand_kg=100.0,
        price_per_kg=0.5,
        capacity=300.0,
    )
    db.add_all([resort, school, composter_primary, composter_fallback])
    db.flush()  # assign buyer ids for User FKs and contract reference

    # ------------------------------------------------------------------ #
    # Users (4): 1 admin + 3 buyer-types, all bcrypt-hashed.
    # Buyer-type Users carry a buyer_id linking them to their Buyer row.
    # ------------------------------------------------------------------ #
    admin = User(
        email="admin@trace.demo",
        password_hash=pw,
        role=UserRole.admin,
        buyer_id=None,
    )
    resort_user = User(
        email="resort@trace.demo",
        password_hash=pw,
        role=UserRole.premium_buyer,
        buyer_id=resort.id,
    )
    school_user = User(
        email="school@trace.demo",
        password_hash=pw,
        role=UserRole.secondary_buyer,
        buyer_id=school.id,
    )
    composter_user = User(
        email="compost@trace.demo",
        password_hash=pw,
        role=UserRole.composter,
        buyer_id=composter_primary.id,
    )
    db.add_all([admin, resort_user, school_user, composter_user])

    # ------------------------------------------------------------------ #
    # Farmers (6): spread around Kingston, JA; all grow tomatoes; each
    # has a distinct telegram_chat_id.
    # ------------------------------------------------------------------ #
    farmers = [
        Farmer(
            name="Farm Alpha",
            telegram_chat_id="tg-1001",
            lat=18.00,
            lng=-76.80,
        ),
        Farmer(
            name="Farm Bravo",
            telegram_chat_id="tg-1002",
            lat=18.01,
            lng=-76.81,
        ),
        Farmer(
            name="Farm Charlie",
            telegram_chat_id="tg-1003",
            lat=17.99,
            lng=-76.79,
        ),
        Farmer(
            name="Farm Delta",
            telegram_chat_id="tg-1004",
            lat=18.02,
            lng=-76.82,
        ),
        Farmer(
            name="Farm Echo",
            telegram_chat_id="tg-1005",
            lat=17.98,
            lng=-76.83,
        ),
        Farmer(
            name="Farm Foxtrot",
            telegram_chat_id="tg-1006",
            lat=18.03,
            lng=-76.78,
        ),
    ]
    db.add_all(farmers)
    db.flush()  # assign farmer ids for the batch FK

    # ------------------------------------------------------------------ #
    # Contract (1): resort (premium) buys 200kg grade-A tomato at $4/kg.
    # ------------------------------------------------------------------ #
    contract = Contract(
        buyer_id=resort.id,
        crop="tomato",
        grade="A",
        kg_target=200.0,
        price_per_kg=4.0,
        deadline=DEADLINE,
        status=ContractStatus.open,
    )
    db.add(contract)

    # ------------------------------------------------------------------ #
    # Batch (1): 30kg grade-A tomato at GRADED_FARM, ready to flow. Linked
    # to the first farmer. decay_on_handoff=True makes this the batch the
    # spec's demo "pre-sets to decay" at handoff grading. The capture_token
    # column is NOT NULL + unique, so generate a real one via the auth
    # helper (which writes + commits the token on the batch row).
    # ------------------------------------------------------------------ #
    batch = Batch(
        farmer_id=farmers[0].id,
        crop="tomato",
        kg=30.0,
        lat=farmers[0].lat,
        lng=farmers[0].lng,
        # Bootstrap only: status set directly to a real State value. In
        # production every status change goes through statemachine.transition;
        # for seed we are intentionally bypassing that to park a ready batch.
        status=State.GRADED_FARM.value,
        farm_grade="A",
        handoff_grade=None,
        final_grade=None,
        decay_event=None,
        photo_ref=None,
        grade_reason_farm="uniform ripe, no damage",
        grade_reason_handoff=None,
        # capture_token is NOT NULL — a placeholder satisfies the column until
        # generate_capture_token overwrites it (and commits) just below.
        capture_token="pending-seed-token",
        capture_token_expires_at=None,
        decay_on_handoff=True,
    )
    db.add(batch)
    db.flush()  # persist the row so generate_capture_token can update it

    # generate_capture_token writes + commits the token; the placeholder above
    # is replaced with a real urlsafe token and a 24h expiry.
    generate_capture_token(db, batch)

    db.commit()


def main(argv: list[str] | None = None) -> None:
    """CLI entrypoint: parse ``--reset``, wire the engine, and run the seed.

    ``--reset`` drops and recreates every table on the configured engine
    before seeding — this is the main path for a clean demo DB. Without it,
    seed inserts run against the existing schema (duplicates will surface as
    IntegrityErrors on unique columns; ``--reset`` is the supported mode).
    """
    parser = argparse.ArgumentParser(
        description="Seed the TRACE deterministic demo scenario."
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Drop and recreate all tables before seeding (clean slate).",
    )
    args = parser.parse_args(argv)

    if args.reset:
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)

    db = SessionLocal()
    try:
        run_seed(db)
    finally:
        db.close()

    print("Seed complete.")


if __name__ == "__main__":
    main()

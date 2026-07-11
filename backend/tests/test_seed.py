"""Tests for the deterministic seed (Task 6).

Calls ``run_seed`` directly against the in-memory SQLite ``db_session`` fixture
(no CLI, no argparse, no Postgres) and asserts the shape of the seeded
scenario: 4 users, 6 farmers, 4 buyers (2 composters), 1 contract, and 1 batch
parked at GRADED_FARM with ``decay_on_handoff=True`` and a valid capture token.
"""

from app.models import (
    Batch,
    Buyer,
    BuyerType,
    Contract,
    Farmer,
    User,
    UserRole,
)
from app.seed import DEMO_PASSWORD, run_seed
from app.statemachine import State


def test_seed_creates_full_scenario(db_session):
    """run_seed inserts the expected counts and the decay batch."""
    run_seed(db_session)

    users = db_session.query(User).all()
    farmers = db_session.query(Farmer).all()
    buyers = db_session.query(Buyer).all()
    contracts = db_session.query(Contract).all()
    batches = db_session.query(Batch).all()

    # --- counts -----------------------------------------------------------
    assert len(users) == 4, f"expected 4 users, got {len(users)}"
    assert len(farmers) == 6, f"expected 6 farmers, got {len(farmers)}"
    assert len(buyers) == 4, f"expected 4 buyers, got {len(buyers)}"
    assert len(contracts) == 1, f"expected 1 contract, got {len(contracts)}"
    assert len(batches) == 1, f"expected 1 batch, got {len(batches)}"

    # --- 2 composters -----------------------------------------------------
    composters = [b for b in buyers if b.type == BuyerType.composter]
    assert len(composters) == 2, (
        f"expected 2 composters, got {len(composters)}"
    )


def test_seed_users_are_bcrypt_hashed_and_known(db_session):
    """All 4 users have bcrypt hashes; emails are the known demo identifiers."""
    run_seed(db_session)

    by_email = {u.email: u for u in db_session.query(User).all()}
    assert set(by_email) == {
        "admin@trace.demo",
        "resort@trace.demo",
        "school@trace.demo",
        "compost@trace.demo",
    }

    for user in by_email.values():
        # bcrypt hashes always start with $2 and are not the raw password.
        assert user.password_hash.startswith("$2"), (
            f"{user.email} password is not a bcrypt hash"
        )
        assert user.password_hash != DEMO_PASSWORD


def test_seed_roles_and_buyer_links(db_session):
    """One admin, one of each buyer role; buyer-type Users link to a Buyer."""
    run_seed(db_session)

    users = db_session.query(User).all()
    roles = {u.role for u in users}
    assert roles == {
        UserRole.admin,
        UserRole.premium_buyer,
        UserRole.secondary_buyer,
        UserRole.composter,
    }, f"role set mismatch: {roles}"

    admin = next(u for u in users if u.role == UserRole.admin)
    assert admin.buyer_id is None, "admin must not be linked to a buyer"

    buyer_users = [u for u in users if u.role != UserRole.admin]
    for u in buyer_users:
        assert u.buyer_id is not None, (
            f"{u.email} ({u.role}) is missing buyer_id"
        )
        # The linked buyer must exist and the buyer's type must match the
        # user's role (premium_buyer->premium, secondary_buyer->secondary,
        # composter->composter).
        buyer = db_session.get(Buyer, u.buyer_id)
        assert buyer is not None, f"{u.email} buyer_id {u.buyer_id} dangling"
        expected_type = {
            UserRole.premium_buyer: BuyerType.premium,
            UserRole.secondary_buyer: BuyerType.secondary,
            UserRole.composter: BuyerType.composter,
        }[u.role]
        assert buyer.type == expected_type, (
            f"{u.email} -> buyer type {buyer.type}, expected {expected_type}"
        )


def test_seed_farmers_have_distinct_chat_ids(db_session):
    """Every farmer has a unique telegram_chat_id (for distinct Telegram chats)."""
    run_seed(db_session)

    farmers = db_session.query(Farmer).all()
    chat_ids = [f.telegram_chat_id for f in farmers]
    assert all(cid is not None for cid in chat_ids), "a farmer has no chat id"
    assert len(set(chat_ids)) == 6, (
        f"expected 6 distinct chat ids, got {len(set(chat_ids))}"
    )


def test_seed_contract_targets_resort(db_session):
    """The single contract is the resort buying 200kg grade-A tomato at $4/kg."""
    run_seed(db_session)

    contract = db_session.query(Contract).one()
    resort = db_session.query(Buyer).filter_by(type=BuyerType.premium).one()

    assert contract.buyer_id == resort.id
    assert contract.crop == "tomato"
    assert contract.grade == "A"
    assert contract.kg_target == 200.0
    assert contract.price_per_kg == 4.0
    assert contract.status.value == "open"


def test_seed_batch_at_graded_farm_and_marked_to_decay(db_session):
    """The seeded batch is at GRADED_FARM, decay_on_handoff=True, token valid."""
    run_seed(db_session)

    batch = db_session.query(Batch).one()
    assert batch.status == State.GRADED_FARM.value, (
        f"batch status {batch.status!r}, expected GRADED_FARM"
    )
    assert batch.farm_grade == "A"
    assert batch.crop == "tomato"
    assert batch.decay_on_handoff is True, (
        "decay batch must have decay_on_handoff=True"
    )
    # capture_token is NOT NULL unique — confirm a real value was written and
    # is not the placeholder used during construction.
    assert batch.capture_token, "capture_token is empty"
    assert batch.capture_token != "pending-seed-token", (
        "capture_token was not replaced by generate_capture_token"
    )
    assert batch.capture_token_expires_at is not None, (
        "capture_token has no expiry"
    )
    # The batch is linked to the first seeded farmer.
    assert batch.farmer_id is not None
    farmer = db_session.get(Farmer, batch.farmer_id)
    assert farmer is not None


def test_seed_run_idempotent_on_reset(db_session):
    """run_seed produces the full scenario from an empty DB each time.

    The supported re-run path for the CLI is ``--reset`` (drop+create+seed);
    tests simulate that by calling run_seed on a fresh empty session. This
    confirms a single clean run produces the expected counts and that the
    deterministic emails are exactly the known set.
    """
    run_seed(db_session)

    assert db_session.query(User).count() == 4
    assert db_session.query(Farmer).count() == 6
    assert db_session.query(Buyer).count() == 4
    assert db_session.query(Batch).count() == 1

    emails = {u.email for u in db_session.query(User).all()}
    assert emails == {
        "admin@trace.demo",
        "resort@trace.demo",
        "school@trace.demo",
        "compost@trace.demo",
    }

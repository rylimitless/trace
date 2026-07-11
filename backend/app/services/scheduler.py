"""Background scheduler — gates ``shipped -> graded_handoff`` (the demo "spoilage clock").

After a batch is SHIPPED, a background task waits a few seconds (demo pacing —
there is no real wall-clock shelf-life timer in the MVP; spec §impl) and then
runs the handoff re-grade via :func:`app.services.handoff.run_handoff`.

The scheduler is single-process asyncio (no external broker). It is started on
app startup and polls for SHIPPED batches with no handoff grade yet. Each batch
is handed off exactly once (the transition out of SHIPPED prevents re-runs).
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import Batch
from app.services.handoff import run_handoff
from app.statemachine import State

log = logging.getLogger(__name__)

# Demo pacing: seconds between SHIPPED and the handoff re-grade. Short so the
# cascade plays in ~3 minutes, not 20 (spec §impl reminder).
HANDOFF_DELAY_SECONDS = 8.0
# How often the poller scans for new SHIPPED batches.
POLL_INTERVAL_SECONDS = 2.0

_task: asyncio.Task | None = None
# Batch ids currently scheduled for handoff (avoids double-scheduling across
# polls). Cleared as each handoff completes.
_scheduled: set[int] = set()


def _shipped_batches_without_handoff(db: Session) -> list[Batch]:
    return (
        db.query(Batch)
        .filter(Batch.status == State.SHIPPED.value)
        .filter(Batch.handoff_grade.is_(None))
        .all()
    )


async def _handoff_after_delay(batch_id: int) -> None:
    """Wait the demo delay, then run the handoff for one batch."""
    await asyncio.sleep(HANDOFF_DELAY_SECONDS)
    db = SessionLocal()
    try:
        batch = db.get(Batch, batch_id)
        if batch is None or batch.status != State.SHIPPED.value:
            return  # already advanced or gone
        run_handoff(db, batch)
    except Exception:  # noqa: BLE001 — a single batch must not kill the poller
        log.exception("run_handoff failed for batch %s", batch_id)
    finally:
        db.close()
        _scheduled.discard(batch_id)


async def _poll_loop() -> None:
    """Continuously schedule handoffs for newly-SHIPPED batches."""
    while True:
        try:
            db = SessionLocal()
            try:
                for batch in _shipped_batches_without_handoff(db):
                    if batch.id in _scheduled:
                        continue
                    _scheduled.add(batch.id)
                    asyncio.create_task(_handoff_after_delay(batch.id))
            finally:
                db.close()
        except Exception:  # noqa: BLE001
            log.exception("handoff poll loop iteration failed")
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


def start() -> None:
    """Start the background poller (idempotent). Call on app startup."""
    global _task
    if _task is not None and not _task.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running loop (e.g. sync test import) — nothing to start.
        return
    _task = loop.create_task(_poll_loop())
    log.info("handoff scheduler started (delay=%.1fs)", HANDOFF_DELAY_SECONDS)


def stop() -> None:
    """Cancel the background poller (for tests/shutdown)."""
    global _task
    if _task is not None:
        _task.cancel()
        _task = None

"""Messaging service — Slice B (spec §4 — farmer intake & comms).

Three functions:

* :func:`store_photo(batch_id, image_bytes)` — persists a farm photo to disk
  and returns the filesystem path. The path is stored on ``batch.photo_ref``.
* :func:`get_batch_photo(batch)` — reads a previously stored photo from disk.
* :func:`send_message(chat_id, text)` — sends a plain Telegram message via
  the Bot API. Best-effort; failures are logged, never raised.
* :func:`send_farmer_update(chat_id, event)` — sends a category-framed update
  (grade + outcome + market_category) to a farmer, never naming a buyer.

Design rules (spec §4a — visibility):
- All outbound messages use *only* grade + outcome + market_category.
- Never name a buyer, contract, or destination.
- ``send_message`` and ``send_farmer_update`` are best-effort — failures are
  logged, never raised (the DB state is the source of truth).
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from app.config import settings

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Photo storage
# ---------------------------------------------------------------------------

# Where uploaded farm photos land on disk.
PHOTO_DIR = os.path.join(os.path.dirname(__file__), "..", "photos")


def store_photo(batch_id: int, image_bytes: bytes) -> str:
    """Persist ``image_bytes`` to disk and return the absolute path.

    The path is intended to be stored on ``batch.photo_ref``. Creates the
    ``PHOTO_DIR`` directory if it does not exist.
    """
    os.makedirs(PHOTO_DIR, exist_ok=True)
    path = os.path.join(PHOTO_DIR, f"batch_{batch_id}.jpg")
    with open(path, "wb") as f:
        f.write(image_bytes)
    return path


def get_batch_photo(batch: Any) -> bytes:
    """Read the stored farm photo for ``batch`` and return raw bytes.

    Args:
        batch: a ``Batch`` ORM instance with ``photo_ref`` set.

    Returns:
        Raw image bytes (JPEG).

    Raises:
        FileNotFoundError: if the photo file is missing from disk.
    """
    with open(batch.photo_ref, "rb") as f:
        return f.read()


# ---------------------------------------------------------------------------
# Telegram outbound messaging
# ---------------------------------------------------------------------------

_TELEGRAM_API_BASE = "https://api.telegram.org"


def _bot_url() -> str:
    """Return ``https://api.telegram.org/bot{token}``."""
    token = settings.telegram_bot_token
    if not token:
        log.warning("telegram_bot_token is not set — messages will not be sent")
    return f"{_TELEGRAM_API_BASE}/bot{token}"


def send_message(chat_id: str, text: str) -> None:
    """Send a plain text message to a Telegram chat.

    Best-effort: failures are logged, never raised. The DB + audit trail are
    the source of truth.
    """
    url = f"{_bot_url()}/sendMessage"
    try:
        resp = httpx.post(
            url,
            json={"chat_id": chat_id, "text": text},
            timeout=10,
        )
        resp.raise_for_status()
    except Exception as exc:
        log.warning("send_message to %s failed: %s", chat_id, exc)


def send_farmer_update(chat_id: str, event: dict) -> None:
    """Send a category-framed batch update to a farmer (spec §4a visibility).

    ``event`` keys (all optional; best-effort rendering):
        - ``crop`` (str)
        - ``farm_grade`` (str)
        - ``handoff_grade`` (str)
        - ``market_category`` (str) — e.g. ``"secondary_market"``, ``"composted"``
        - ``payout_was`` (float | None) — expected premium payout
        - ``payout_now`` (float | None) — actual payout
        - ``reason_code`` (str | None) — e.g. ``"transit_decay"``

    The message is grade + outcome + category-framed. Never names a buyer.
    Best-effort: logged on failure, never raised.
    """
    crop = event.get("crop", "produce")
    handoff = event.get("handoff_grade", "")
    category = (event.get("market_category") or "market").replace("_", " ")
    payout_now = event.get("payout_now")
    payout_was = event.get("payout_was")

    parts = [
        f"Your {crop}",
    ]
    if handoff:
        parts.append(f"graded {handoff} at handoff")
    parts.append(f"and went to the {category}")

    if payout_now is not None and payout_was is not None:
        parts.append(
            f"\u2014 ${payout_now:.2f} instead of ${payout_was:.2f}"
        )
    elif payout_now is not None:
        parts.append(f"\u2014 ${payout_now:.2f}")

    parts.append("Still sold, nothing wasted.")
    text = " ".join(parts)

    send_message(chat_id, text)

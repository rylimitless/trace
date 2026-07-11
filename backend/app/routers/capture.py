"""Capture route (spec §4 — farmer Telegram upload).

``POST /capture/{token}`` is the only unauthenticated route in TRACE: the
token in the URL *is* the auth (it is per-batch and time-boxed).

Flow:
  1. Look up ``Batch`` by capture token, verify not expired.
  2. Accept the uploaded photo (multipart/form-data).
  3. Store the photo via :func:`app.services.messaging.store_photo`.
  4. Call :func:`app.services.grading.grade` to grade the photo.
  5. Set ``batch.farm_grade`` and ``batch.grade_reason_farm``.
  6. Transition ``batch HARVESTED -> GRADED_FARM`` through the state machine.
  7. Return ``{grade, reason}`` to the upload page so the farmer sees their grade.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Batch
from app.services.grading import grade
from app.services.messaging import store_photo
from app.statemachine import State, transition

router = APIRouter(tags=["capture"])


@router.post("/capture/{token}")
def capture(token: str, file: UploadFile, db: Session = Depends(get_db)):
    """Farmer uploads a capture for a batch, authenticated by the URL token.

    The token must match a ``Batch.capture_token`` and must not be expired
    (``capture_token_expires_at > now``). Accepts a single image file via the
    ``file`` multipart field.

    Returns ``{"grade": "...", "reason": "..."}``.
    """
    # 1. Resolve the batch by token
    batch: Batch | None = (
        db.query(Batch)
        .filter(Batch.capture_token == token)
        .first()
    )
    if batch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invalid or unknown capture token",
        )

    if (
        batch.capture_token_expires_at is not None
        and batch.capture_token_expires_at < datetime.now(timezone.utc)
    ):
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Capture token has expired",
        )

    # 2. Read the uploaded file
    image_bytes = file.file.read()

    # 3. Store the photo on disk
    photo_path = store_photo(batch.id, image_bytes)
    batch.photo_ref = photo_path
    db.flush()

    # 4. Grade the photo
    try:
        result = grade(image_bytes, crop=batch.crop or "tomato")
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Grading failed: {exc}",
        ) from exc

    # 5. Set farm grade + reason
    batch.farm_grade = result["grade"]
    batch.grade_reason_farm = result.get("reason", "")

    # 6. Transition HARVESTED -> GRADED_FARM
    transition(db, batch, State.GRADED_FARM, farm_grade=result["grade"])

    return {
        "grade": result["grade"],
        "reason": result.get("reason", ""),
    }

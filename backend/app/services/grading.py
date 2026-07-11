"""
Grading & Decay — the cleanest slice in TRACE.

Exports:
    grade(image_bytes, crop) -> {"grade": "A"|"B"|"WASTE", "reason": "..."}
    simulate_decay(image_bytes) -> image_bytes

Seam documentation:
    Slice B calls grade() at intake for the farm grade.
    Slice D calls grade(simulate_decay(photo), crop) at handoff for the re-grade.

This module knows NOTHING of Telegram, routing, payouts, or the database.
It is a pure function of (image_bytes, crop) → {grade, reason}.
"""

import base64
import io
import json
import logging

import httpx
from PIL import Image, ImageEnhance, ImageFilter

from app.config import settings

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────
#  The Golden Prompt — fixed, immutable, the grading standard
# ─────────────────────────────────────────────────────────────────
#  Anchored to USDA §51.1855–1859 (United States Standards for
#  Grades of Fresh Tomatoes). Temperature 0. JSON-only output.
#  This is the ONLY prompt this module ever sends. Do not change
#  without updating the golden-image tests.
# ─────────────────────────────────────────────────────────────────

GRADE_PROMPT = """You are a produce quality grader following the USDA United States
Standards for Grades of Fresh Tomatoes (§51.1855–1859). A coin is in
the frame as a size reference. Grade this batch of tomatoes by the USDA
definitions, using visible SIZE (vs the coin), MATURITY (color/ripeness),
and DEFECTS (cuts, bruising, growth cracks, soft/wrinkled spots, decay, mold):

- A     = U.S. No. 1 — fairly uniform ripe color, ~free from damage
- B     = U.S. No. 2 — tolerable defects, free from serious damage
- WASTE = below No. 2 — decay / severe damage / unsellable

Reply ONLY: {"grade":"A"|"B"|"WASTE",
              "reason":"one sentence citing the USDA deciding factor"}"""

VALID_GRADES = frozenset({"A", "B", "WASTE"})

# ─────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────


def _image_to_data_uri(image_bytes: bytes) -> str:
    """Convert raw image bytes to a base64 data URI for the vision API."""
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    return f"data:image/jpeg;base64,{b64}"


def _parse_and_validate(raw_text: str) -> dict:
    """Parse JSON from the LLM and enforce the grade is in {A, B, WASTE}."""
    result = json.loads(raw_text)

    grade_value = result.get("grade")
    if grade_value not in VALID_GRADES:
        raise ValueError(
            f"LLM returned invalid grade {grade_value!r}. "
            f"Raw response: {raw_text}"
        )

    reason = result.get("reason")
    if not reason or not isinstance(reason, str) or len(reason.strip()) < 3:
        raise ValueError(
            f"LLM returned empty or missing reason. Raw response: {raw_text}"
        )

    return {"grade": grade_value, "reason": reason.strip()}


def _call_openrouter(image_bytes: bytes) -> dict:
    """Send the image + golden prompt to OpenRouter; return parsed result.

    Two attempts (one retry on failure). Raises RuntimeError if both fail.
    """
    last_error: Exception | None = None

    for attempt in (1, 2):
        try:
            resp = httpx.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.openrouter_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": settings.openrouter_model,
                    "temperature": 0,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": GRADE_PROMPT},
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": _image_to_data_uri(image_bytes)
                                    },
                                },
                            ],
                        }
                    ],
                },
                timeout=60,
            )
            resp.raise_for_status()

            content = resp.json()["choices"][0]["message"]["content"]
            result = _parse_and_validate(content)
            return result

        except Exception as exc:
            last_error = exc
            logger.warning(
                "grade attempt %d failed: %s", attempt, exc, exc_info=True
            )
            if attempt == 1:
                continue  # one retry

    raise RuntimeError(
        f"grade() failed after 2 attempts. Last error: {last_error}"
    ) from last_error


# ─────────────────────────────────────────────────────────────────
#  Public API
# ─────────────────────────────────────────────────────────────────


def grade(image_bytes: bytes, crop: str = "tomato") -> dict:
    """Grade a produce photo using the vision LLM (temp 0, USDA-anchored).

    Args:
        image_bytes: Raw JPEG/PNG bytes of the produce photo.
                     Must include a coin in-frame for size reference.
        crop:        Crop type. MVP only supports "tomato". Other values
                     accepted for forward-compatibility but are not yet
                     wired to crop-specific prompts.

    Returns:
        {"grade": "A"|"B"|"WASTE", "reason": "one short sentence"}

    Raises:
        RuntimeError: LLM call failed after retry, or returned invalid grade.
        ValueError:   image_bytes is empty or not a valid image.
    """
    if not image_bytes:
        raise ValueError("image_bytes must not be empty")

    return _call_openrouter(image_bytes)


def simulate_decay(image_bytes: bytes) -> bytes:
    """Degrade an image to simulate transit spoilage (darkening, browning,
    softening). Used by Slice D at handoff to simulate time passing between
    farm-pickup and buyer-dropoff.

    The degradation applies three PIL operations in sequence:
      1. Darken  (Brightness 0.75) — bruising, light-loss in the truck
      2. Brown   (Color 0.6)       — over-ripening, chlorophyll breakdown
      3. Soften  (GaussianBlur 1)  — skin-wrinkle, soft-spot onset

    The degraded image, when re-graded via grade(), should score ≤ the
    original farm grade.  This is verified by test_decay_lowers_grade.

    Args:
        image_bytes: Raw image bytes of the original farm photo.

    Returns:
        Raw JPEG bytes of the artificially "aged" image.
    """
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")

    # 1. Darken — reduced brightness from stacking / shade in transit
    img = ImageEnhance.Brightness(img).enhance(0.75)

    # 2. Desaturate / brown — over-ripening
    img = ImageEnhance.Color(img).enhance(0.6)

    # 3. Soften — skin wrinkles, soft spots forming
    img = img.filter(ImageFilter.GaussianBlur(radius=1))

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()

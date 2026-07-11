"""Grading service — Slice C (spec §8).

Two stateless functions:

* :func:`grade(image_bytes, crop)` — calls OpenRouter vision LLM with the fixed
  USDA-anchored prompt at temp 0, returns ``{grade, reason}``.
* :func:`simulate_decay(image_bytes)` — PIL-degrades an image to simulate
  transit spoilage (darkening + soft-spot artifacts).

The prompt is the grading standard (spec §8.1). It is fixed, one string, temp 0.
"""

from __future__ import annotations

import base64
import json
import logging

import httpx
from PIL import Image, ImageDraw, ImageFilter, ImageEnhance

from app.config import settings

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# The fixed USDA grading prompt (Golden Prompt, PROMPTS.md, verbatim).
# ---------------------------------------------------------------------------

GRADE_PROMPT = """You are a produce quality grader following the USDA United States
Standards for Grades of Fresh Tomatoes (§51.1855–1859). A coin is in
the frame as a size reference. Grade this batch of tomatoes by the
USDA definitions, using visible SIZE (vs the coin), MATURITY
(color/ripeness), and DEFECTS (cuts, bruising, growth cracks,
soft/wrinkled spots, decay, mold):

- A     = U.S. No. 1 — fairly uniform ripe color, ~free from damage
- B     = U.S. No. 2 — tolerable defects, free from serious damage
- WASTE = below No. 2 — decay / severe damage / unsellable

Reply ONLY: {"grade":"A"|"B"|"WASTE",
              "reason":"one sentence citing the USDA deciding factor"}"""

_DEFAULT_MODEL = "openai/gpt-4.1-nano"
_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


# ---------------------------------------------------------------------------
# grade()
# ---------------------------------------------------------------------------


def grade(image_bytes: bytes, crop: str = "tomato") -> dict:
    """Grade a produce photo via the vision LLM.

    Args:
        image_bytes: raw image bytes (JPEG/PNG from the farmer's upload).
        crop: crop name (MVP supports ``tomato``; other values use the same
            USDA fixed prompt as a reasonable approximation).

    Returns:
        ``{"grade": "A" | "B" | "WASTE", "reason": "..."}``

    Raises:
        RuntimeError: if the LLM call fails after one retry, or if the
            response is malformed (not valid JSON with the expected keys).
    """
    # Encode the image as a base64 data URL
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    data_url = f"data:image/jpeg;base64,{b64}"

    payload = {
        "model": settings.openrouter_model or _DEFAULT_MODEL,
        "temperature": 0,
        "max_tokens": 200,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": GRADE_PROMPT},
                    {
                        "type": "image_url",
                        "image_url": {"url": data_url},
                    },
                ],
            }
        ],
    }

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {settings.openrouter_api_key}",
    }

    # Retry once on network/HTTP errors
    for attempt in range(2):
        try:
            resp = httpx.post(
                _OPENROUTER_URL,
                headers=headers,
                json=payload,
                timeout=30,
            )
            resp.raise_for_status()
            body = resp.json()
            raw = body["choices"][0]["message"]["content"].strip()

            # Strip markdown fences if the model wraps it
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[-1].rsplit("\n", 1)[0]

            result = json.loads(raw)
            grade_val = result.get("grade")
            reason = result.get("reason", "")

            if grade_val not in ("A", "B", "WASTE"):
                raise ValueError(f"unexpected grade: {grade_val}")

            return {"grade": grade_val, "reason": reason}

        except Exception as exc:
            log.warning(
                "grade() attempt %d failed for %s: %s", attempt + 1, crop, exc
            )
            if attempt == 1:
                raise RuntimeError(
                    f"grading failed after retry: {exc}"
                ) from exc

    # Should not be reached
    raise RuntimeError("grading failed (unreachable)")


# ---------------------------------------------------------------------------
# simulate_decay()
# ---------------------------------------------------------------------------


def simulate_decay(image_bytes: bytes) -> bytes:
    """Degrade an image to simulate transit spoilage (spec §8.3).

    Applies PIL transformations: darken, reduce saturation to make the produce
    look less fresh, add soft dark blotches (decay spots), and a slight blur.

    This is the MVP's image-level "decay" — no second photo needed. The handoff
    pass calls ``grade(simulate_decay(original), crop)`` on decay-flagged batches.

    Returns JPEG bytes.
    """
    img = Image.open(io_bytes(image_bytes)).convert("RGB")

    # 1. Darken slightly
    enhancer = ImageEnhance.Brightness(img)
    img = enhancer.enhance(0.75)

    # 2. Reduce saturation
    enhancer = ImageEnhance.Color(img)
    img = enhancer.enhance(0.5)

    # 3. Add soft dark blotches (simulated decay spots)
    draw = ImageDraw.Draw(img)
    w, h = img.size
    import random

    rng = random.Random(42)  # deterministic seed for reproducibility
    for _ in range(rng.randint(3, 6)):
        cx = rng.randint(int(w * 0.1), int(w * 0.9))
        cy = rng.randint(int(h * 0.1), int(h * 0.9))
        radius = rng.randint(int(min(w, h) * 0.03), int(min(w, h) * 0.08))
        # Semi-transparent dark blotch
        for i in range(3):
            r = radius - i * 3
            if r > 0:
                draw.ellipse(
                    [cx - r, cy - r, cx + r, cy + r],
                    fill=(60 - i * 10, 40 - i * 5, 30 - i * 5),
                )

    # 4. Slight blur to simulate softening
    img = img.filter(ImageFilter.GaussianBlur(radius=1.5))

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def io_bytes(data: bytes):
    """Return a BytesIO for ``data``. Defined as a function so PIL import
    works cleanly at module level."""
    from io import BytesIO
    return BytesIO(data)

"""Routing-justification message generator (spec §4 — Slice D).

``justify(decision, payout)`` writes ONE kind farmer-facing message explaining
a reroute, via a single OpenRouter call. It is the second LLM surface in TRACE
(the first being produce grading). The rules engine has already decided the
destination; the LLM only *explains* it in farmer-safe language.

Design rules (spec §4a — visibility):
- The message may use only ``grade + outcome + market_category``.
- It must NEVER name a buyer, contract, or destination. ``payout.destination``
  is internal-only; ``market_category`` (the buyer *type* category) is the
  farmer-facing handle.
- On ANY failure (httpx error, empty key, bad response) we fall back to a
  deterministic template and never raise — the farmer always gets a message.
- The single network call lives behind :func:`_call_llm` so tests monkeypatch
  that one seam instead of ``httpx`` directly.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from app.config import settings
from app.models import Payout, RoutingDecision

# The fixed routing-justification prompt (PROMPTS.md, verbatim). temperature 0
# for reproducibility — the same decision yields the same message.
_PROMPT = """You write clear, kind farmer messages. Given a routing decision as JSON: {reason_code, crop, farm_grade, handoff_grade, market_category, payout_was, payout_now}
Write ONE message to the farmer. Rules:
- Use only grade + outcome + market_category (e.g. "secondary market").
- NEVER name a specific buyer, contract, or destination.
- If money changed, state both the old and new amount.
- Reassuring tone; under 40 words.
"""

_DEFAULT_MODEL = "openai/gpt-4o-mini"
_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


def _call_llm(
    prompt: str, user_json: dict[str, Any], model: str, api_key: str
) -> str:
    """Make the single OpenRouter POST and return the message text.

    Isolated as one function so tests monkeypatch this seam. Raises on any
    failure (empty key, network error, bad response); :func:`justify` catches
    and falls back. An empty key is treated as unavailable so production never
    makes an unauthenticated call, but tests that monkeypatch this seam still
    drive the happy path.
    """
    if not api_key:
        raise RuntimeError("openrouter_api_key is empty")
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    payload = {
        "model": model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps(user_json)},
        ],
    }
    resp = httpx.post(_OPENROUTER_URL, headers=headers, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"]


def justify(
    decision: RoutingDecision,
    payout: Payout,
    farm_grade: str | None = None,
) -> str:
    """Return a kind farmer-facing message for a reroute.

    Builds the structured input from ``decision`` + ``payout``, calls the LLM
    once, and returns the stripped message. On any failure (or an empty API
    key) falls back to a deterministic template — never raises.
    """
    market_category = payout.market_category.value if payout.market_category else None

    user_json: dict[str, Any] = {
        "reason_code": (
            decision.reason_code.value if decision.reason_code else None
        ),
        "market_category": market_category,
        "kg": payout.kg,
        "payout_amount": str(payout.amount) if payout.amount is not None else None,
        "farm_grade": farm_grade,
    }

    model = settings.llm_justification_model or _DEFAULT_MODEL
    api_key = settings.openrouter_api_key

    try:
        text = _call_llm(_PROMPT, user_json, model, api_key)
        if text:
            return text.strip()
        raise RuntimeError("empty LLM response")
    except Exception:
        return _fallback_message(payout, market_category)


def _fallback_message(payout: Payout, market_category: str | None) -> str:
    """Deterministic, buyer-safe template used when the LLM is unavailable.

    Uses only grade + market_category + amount — no buyer/destination. The
    crop defaults to "produce" when unknown. ``market_category`` is rendered
    human-friendly (``secondary_market`` -> ``secondary market``).
    """
    crop = None
    batch = getattr(payout, "batch", None)
    if batch is not None and getattr(batch, "crop", None):
        crop = batch.crop
    handoff_grade = payout.grade_paid_at
    market_label = (market_category or "market").replace("_", " ")
    amount = payout.amount if payout.amount is not None else "amount"
    return (
        f"Your {crop or 'produce'} dropped to Grade {handoff_grade or '?'} "
        f"and sold to the {market_label} for {amount}. Still sold, nothing wasted."
    )

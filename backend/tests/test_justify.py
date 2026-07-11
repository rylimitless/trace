"""Tests for app.services.justify.

justify() turns a RoutingDecision + Payout into a kind farmer-facing message
via one OpenRouter call. It must never leak a buyer/destination (spec §4a) and
must fall back to a deterministic template on any failure — never raise.

The single network call lives behind ``justify._call_llm`` so tests monkeypatch
that seam; no real HTTP is made.
"""

import json
from decimal import Decimal

import app.services.justify as justify_mod
from app.models import (
    MarketCategory,
    Payout,
    PayoutStatus,
    ReasonCode,
    RoutingDecision,
)
from app.services.justify import justify


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_decision(to_destination="Acme Wholesale", reason=ReasonCode.transit_decay):
    """A RoutingDecision does not need to be persisted for justify()."""
    return RoutingDecision(
        batch_id=1,
        from_destination="Premium Co.",
        to_destination=to_destination,
        reason_code=reason,
    )


def _make_payout(
    market_category=MarketCategory.secondary_market,
    kg=12.5,
    amount=Decimal("37.50"),
):
    return Payout(
        farmer_id=1,
        batch_id=1,
        grade_paid_at="B",
        destination="Acme Wholesale",  # internal-only, must NOT appear in message
        market_category=market_category,
        kg=kg,
        amount=amount,
        status=PayoutStatus.held,
    )


# ---------------------------------------------------------------------------
# Happy path: LLM returns a message -> justify returns it stripped
# ---------------------------------------------------------------------------


def test_justify_returns_llm_message(monkeypatch):
    captured = {}

    def fake_call_llm(prompt, user_json, model, api_key):
        captured["prompt"] = prompt
        captured["user_json"] = user_json
        captured["model"] = model
        captured["api_key"] = api_key
        return "  Your tomatoes moved to the secondary market. Still sold!  "

    monkeypatch.setattr(justify_mod, "_call_llm", fake_call_llm)

    decision = _make_decision()
    payout = _make_payout()
    msg = justify(decision, payout, farm_grade="A")

    assert msg == "Your tomatoes moved to the secondary market. Still sold!"
    # The structured input carries the fields the spec requires.
    assert captured["user_json"]["reason_code"] == "transit_decay"
    assert captured["user_json"]["market_category"] == "secondary_market"
    assert captured["user_json"]["kg"] == 12.5
    assert captured["user_json"]["payout_amount"] == "37.50"
    assert captured["user_json"]["farm_grade"] == "A"
    # temperature-0 model selection falls back to the cheap default when unset.
    assert captured["model"] == "openai/gpt-4o-mini"
    assert captured["api_key"] == ""  # empty in tests, but still passed through


def test_justify_never_leaks_destination_in_structured_input(monkeypatch):
    captured = {}

    def fake_call_llm(prompt, user_json, model, api_key):
        captured.update(user_json=user_json)
        return "ok"

    monkeypatch.setattr(justify_mod, "_call_llm", fake_call_llm)

    decision = _make_decision(to_destination="Compost Co.")
    payout = _make_payout(market_category=MarketCategory.composted)
    justify(decision, payout)

    # Spec §4a: the farmer-facing structured input must carry only the buyer
    # TYPE category, never the buyer/destination name itself.
    assert "to_destination" not in captured["user_json"]
    assert "destination" not in captured["user_json"]
    assert captured["user_json"]["market_category"] == "composted"
    assert "Compost Co." not in json.dumps(captured["user_json"])


# ---------------------------------------------------------------------------
# Fallback path: any failure -> deterministic template, never raise
# ---------------------------------------------------------------------------


def test_justify_falls_back_when_llm_raises(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(justify_mod, "_call_llm", boom)

    decision = _make_decision(reason=ReasonCode.transit_decay)
    payout = _make_payout(market_category=MarketCategory.secondary_market)
    msg = justify(decision, payout, farm_grade="A")

    # Non-empty template, mentions grade + category, no buyer name leaked.
    assert isinstance(msg, str)
    assert msg.strip() != ""
    assert "Grade" in msg or "grade" in msg
    assert "secondary market" in msg
    # The destination (a buyer name) must never surface to the farmer.
    assert "Acme" not in msg


def test_justify_fallback_uses_handoff_grade_and_amount(monkeypatch):
    monkeypatch.setattr(
        justify_mod, "_call_llm", lambda *a, **k: (_ for _ in ()).throw(Exception("nope"))
    )

    decision = _make_decision()
    payout = _make_payout(
        market_category=MarketCategory.composted, amount=Decimal("5.00")
    )
    msg = justify(decision, payout, farm_grade="A")

    # Template references the handoff grade paid at and the amount + category.
    assert "Grade" in msg
    assert "composted" in msg or "compost" in msg
    assert "5.00" in msg


def test_justify_fallback_with_no_handoff_grade_uses_produce_word(monkeypatch):
    monkeypatch.setattr(justify_mod, "_call_llm", lambda *a, **k: None)

    decision = _make_decision()
    payout = _make_payout()
    # payout.grade_paid_at carries the handoff grade; clear it to exercise the
    # missing-field branch of the template.
    payout.grade_paid_at = None
    msg = justify(decision, payout)

    assert msg.strip() != ""
    # No buyer/destination leaks even in the degraded template.
    assert "Acme" not in msg

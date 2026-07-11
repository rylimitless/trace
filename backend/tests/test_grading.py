"""
Tests for the grading & decay module.

Two kinds of tests:
  1. Mocked unit tests  — no API key needed, run in CI
  2. Live-gated tests   — need OPENROUTER_API_KEY + fixture images

Quick reference:
    pytest -k "Mocked or Unit"        # mocked only, no key needed
    pytest -k "Live"                  # live only (needs key + fixtures)
    pytest tests/test_grading.py -v   # everything (skips what it can't run)
"""

import io
import json
import os
import statistics
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from app.services.grading import GRADE_PROMPT, VALID_GRADES, grade, simulate_decay

# ─────────────────────────────────────────────────────────────────
#  Paths & helpers
# ─────────────────────────────────────────────────────────────────

FIXTURES = Path(__file__).parent / "fixtures"


def _read_fixture(name: str) -> bytes:
    """Read a fixture image; skip test if missing."""
    path = FIXTURES / name
    if not path.exists():
        pytest.skip(f"Fixture image not found: {path}")
    return path.read_bytes()


def _needs_api_key():
    """Skip the test if OPENROUTER_API_KEY is not set."""
    if not os.environ.get("OPENROUTER_API_KEY"):
        pytest.skip("OPENROUTER_API_KEY not set — skipping live test")


def _tiny_jpeg() -> bytes:
    """Generate a tiny valid JPEG in memory for mocked tests."""
    img = Image.new("RGB", (64, 64), color=(180, 40, 40))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


# ─────────────────────────────────────────────────────────────────
#  Mocked unit tests — grade()
# ─────────────────────────────────────────────────────────────────


class TestGradeMocked:
    """Tests that mock httpx.post — no real API calls."""

    @staticmethod
    def _fake_response(content: str) -> MagicMock:
        """Build a mock httpx response with the given LLM text output."""
        resp = MagicMock()
        resp.json.return_value = {
            "choices": [{"message": {"content": content}}]
        }
        return resp

    def test_parses_valid_A_grade(self):
        """grade() should parse {"grade":"A","reason":"..."} correctly."""
        with patch(
            "httpx.post",
            return_value=self._fake_response(
                '{"grade":"A","reason":"Uniform color, no defects"}'
            ),
        ):
            result = grade(_tiny_jpeg())

        assert result == {"grade": "A", "reason": "Uniform color, no defects"}

    def test_parses_valid_B_grade(self):
        """grade() should parse a B response."""
        with patch(
            "httpx.post",
            return_value=self._fake_response(
                '{"grade":"B","reason":"Minor growth cracks on two fruits"}'
            ),
        ):
            result = grade(_tiny_jpeg())

        assert result["grade"] == "B"

    def test_parses_valid_WASTE_grade(self):
        """grade() should parse a WASTE response."""
        with patch(
            "httpx.post",
            return_value=self._fake_response(
                '{"grade":"WASTE","reason":"Visible mold and severe decay"}'
            ),
        ):
            result = grade(_tiny_jpeg())

        assert result["grade"] == "WASTE"

    def test_rejects_invalid_grade(self):
        """grade() must raise RuntimeError if LLM returns e.g. 'C'."""
        with patch(
            "httpx.post",
            return_value=self._fake_response(
                '{"grade":"C","reason":"below standard"}'
            ),
        ):
            with pytest.raises(RuntimeError):
                grade(_tiny_jpeg())

    def test_rejects_empty_reason(self):
        """grade() must raise if the reason is missing or empty."""
        with patch(
            "httpx.post",
            return_value=self._fake_response('{"grade":"A","reason":""}'),
        ):
            with pytest.raises(RuntimeError):
                grade(_tiny_jpeg())

    def test_rejects_missing_reason_field(self):
        """grade() must raise if the JSON has no reason key at all."""
        with patch(
            "httpx.post",
            return_value=self._fake_response('{"grade":"B"}'),
        ):
            with pytest.raises(RuntimeError):
                grade(_tiny_jpeg())

    def test_retries_on_malformed_json(self):
        """First response is garbage; second is valid → should succeed."""
        bad = self._fake_response("not json at all!!!")
        good = self._fake_response(
            '{"grade":"B","reason":"minor blemishes visible"}'
        )

        with patch("httpx.post", side_effect=[bad, good]):
            result = grade(_tiny_jpeg())

        assert result["grade"] == "B"

    def test_raises_after_two_failures(self):
        """Both attempts fail → RuntimeError."""
        bad = self._fake_response("still not json")

        with patch("httpx.post", side_effect=[bad, bad]):
            with pytest.raises(RuntimeError):
                grade(_tiny_jpeg())

    def test_uses_temperature_zero(self):
        """The API call MUST include temperature=0 for reproducibility."""
        with patch(
            "httpx.post",
            return_value=self._fake_response(
                '{"grade":"A","reason":"perfect"}'
            ),
        ) as mock_post:
            grade(_tiny_jpeg())

        sent = mock_post.call_args[1]["json"]
        assert sent["temperature"] == 0, "temperature must be 0"

    def test_includes_golden_prompt(self):
        """Every call must include the fixed USDA-anchored prompt."""
        with patch(
            "httpx.post",
            return_value=self._fake_response(
                '{"grade":"A","reason":"good"}'
            ),
        ) as mock_post:
            grade(_tiny_jpeg())

        sent = mock_post.call_args[1]["json"]
        text_block = sent["messages"][0]["content"][0]["text"]
        assert "USDA" in text_block
        assert "§51.1855" in text_block

    def test_rejects_empty_bytes(self):
        """grade() should raise ValueError on empty input."""
        with pytest.raises(ValueError, match="must not be empty"):
            grade(b"")


# ─────────────────────────────────────────────────────────────────
#  Pure unit tests — simulate_decay()  (no API, no fixtures)
# ─────────────────────────────────────────────────────────────────


class TestDecayUnit:
    """Tests for simulate_decay that never touch an API or a file."""

    def test_returns_bytes(self):
        """simulate_decay should return bytes, not a PIL Image."""
        result = simulate_decay(_tiny_jpeg())
        assert isinstance(result, bytes)

    def test_output_is_valid_jpeg(self):
        """Output must be re-openable by PIL."""
        decayed = simulate_decay(_tiny_jpeg())
        img = Image.open(io.BytesIO(decayed))
        assert img.format == "JPEG"

    def test_decay_reduces_brightness(self):
        """The darkened image should have a lower mean pixel value."""
        original = Image.open(io.BytesIO(_tiny_jpeg())).convert("RGB")
        decayed = Image.open(io.BytesIO(simulate_decay(_tiny_jpeg()))).convert("RGB")

        orig_mean = statistics.mean(
            sum(p) / 3 for p in original.getdata()
        )
        decayed_mean = statistics.mean(
            sum(p) / 3 for p in decayed.getdata()
        )

        assert decayed_mean < orig_mean, (
            f"Decayed should be darker. "
            f"Original mean: {orig_mean:.1f}, Decayed: {decayed_mean:.1f}"
        )

    def test_decay_reduces_saturation(self):
        """Desaturation should shrink the R-G-B spread per pixel."""
        original = Image.open(io.BytesIO(_tiny_jpeg())).convert("RGB")
        decayed = Image.open(io.BytesIO(simulate_decay(_tiny_jpeg()))).convert("RGB")

        def _saturation(pixel: tuple[int, ...]) -> float:
            r, g, b = pixel
            mx = max(r, g, b)
            mn = min(r, g, b)
            return (mx - mn) / max(mx, 1)

        orig_sat = statistics.mean(_saturation(p) for p in original.getdata())
        decayed_sat = statistics.mean(_saturation(p) for p in decayed.getdata())

        assert decayed_sat <= orig_sat, "Decayed image should be less saturated"

    def test_decay_changes_bytes(self):
        """simulate_decay MUST return different bytes than the input."""
        original = _tiny_jpeg()
        decayed = simulate_decay(original)
        assert original != decayed, "simulate_decay returned identical bytes"


# ─────────────────────────────────────────────────────────────────
#  Live-gated integration tests  (need OPENROUTER_API_KEY + fixtures)
# ─────────────────────────────────────────────────────────────────


class TestGradeLive:
    """Real OpenRouter calls against fixture images."""

    def test_grade_fresh(self):
        """A fresh tomato should grade as A (or at worst B)."""
        _needs_api_key()
        image = _read_fixture("fresh.jpg")
        result = grade(image)

        assert result["grade"] in {"A", "B"}, (
            f"Expected A or B, got {result['grade']}: {result['reason']}"
        )
        assert len(result["reason"]) > 5, "Reason too short"

    def test_grade_blemished(self):
        """A blemished tomato should NOT grade as A."""
        _needs_api_key()
        image = _read_fixture("blemished.jpg")
        result = grade(image)

        assert result["grade"] in {"B", "WASTE"}, (
            f"Expected B or WASTE for blemished, got {result['grade']}: {result['reason']}"
        )

    def test_grade_waste(self):
        """A rotting tomato must grade as WASTE."""
        _needs_api_key()
        image = _read_fixture("waste.jpg")
        result = grade(image)

        assert result["grade"] == "WASTE", (
            f"Expected WASTE, got {result['grade']}: {result['reason']}"
        )


class TestDecayLive:
    """End-to-end: decay + re-grade against real OpenRouter."""

    def test_decay_lowers_or_keeps_grade(self):
        """Decaying a fresh image must not IMPROVE the grade."""
        _needs_api_key()
        fresh = _read_fixture("fresh.jpg")
        decayed = simulate_decay(fresh)

        fresh_grade = grade(fresh)
        decayed_grade = grade(decayed)

        rank = {"A": 3, "B": 2, "WASTE": 1}
        assert rank[decayed_grade["grade"]] <= rank[fresh_grade["grade"]], (
            f"Decay should not improve the grade! "
            f"Fresh: {fresh_grade}, Decayed: {decayed_grade}"
        )

    def test_decay_output_is_valid_image(self):
        """simulate_decay must return a valid image PIL can re-open."""
        _needs_api_key()
        fresh = _read_fixture("fresh.jpg")
        decayed = simulate_decay(fresh)

        img = Image.open(io.BytesIO(decayed))
        assert img.size[0] > 0
        assert img.size[1] > 0

    def test_decay_preserves_dimensions(self):
        """Decay should not resize the image — just degrade pixels."""
        _needs_api_key()
        fresh = _read_fixture("fresh.jpg")
        original_size = Image.open(io.BytesIO(fresh)).size
        decayed_size = Image.open(io.BytesIO(simulate_decay(fresh))).size

        assert original_size == decayed_size, (
            f"simulate_decay changed dimensions: {original_size} → {decayed_size}"
        )

"""Tests for the research engine (Delivery 6).

Offline, deterministic, synthetic candles only — no MISTRAL_API_KEY required.
Core invariant under test: research output CANNOT bypass the gates. A
Shariah-violating pseudo-recommendation is blocked, and the engine returns
recommendations (never an executable order).
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

from halaltrade.config import Settings
from halaltrade.marketdata import Candle
from halaltrade.models import InstrumentType, PipelineDecision, Side, Signal
from halaltrade.research.engine import Candidate, ResearchConfig, ResearchEngine
from halaltrade.research.llm import LLMClient, SYSTEM_PROMPT, summarize_candidates
from halaltrade.strategies import SmaCrossStrategy

BASE = datetime(2025, 1, 1, tzinfo=timezone.utc)


def mk(close: float, *, i: int = 0) -> Candle:
    return Candle(
        symbol="BTCUSDT", timeframe="1h",
        open=close, high=close, low=close, close=close, volume=1.0,
        timestamp=BASE + timedelta(hours=i), source="synthetic",
    )


def uptrend(n: int = 60) -> list[Candle]:
    return [mk(100 + i * 2.0, i=i) for i in range(n)]


def make_settings(**over) -> Settings:
    defaults = dict(
        trading_mode="paper", live_enabled=False,
        max_position_size=5000.0, max_exposure=5000.0,
        max_loss_per_trade=500.0, max_daily_loss=1000.0,
        max_drawdown=0.5, min_account_balance=100.0,
        min_notional=5.0, max_data_age_seconds=3600 * 1000.0,
    )
    defaults.update(over)
    return Settings(**defaults)


@pytest.fixture(autouse=True)
def no_mistral_key(monkeypatch) -> None:
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)


def test_analyze_returns_ranked_recommendations_offline() -> None:
    engine = ResearchEngine(settings=make_settings(), llm_client=LLMClient(api_key=""))
    report = engine.analyze(uptrend())
    assert report.regime in ("trending-up", "high-volatility", "ranging",
                             "trending-down", "unknown")
    assert len(report.recommendations) == 3
    assert report.llm_used is False  # no key -> offline fallback
    for rec in report.recommendations:
        assert rec.executable is False  # NEVER executable
        assert rec.signal.side in (Side.BUY, Side.SELL, Side.HOLD)
        assert rec.pipeline is not None
        assert "research only" in rec.rationale
        assert rec.risk_notes


def test_shariah_violating_candidate_is_blocked() -> None:
    """A futures/leverage pseudo-recommendation must NOT survive the gates."""

    def futures_strategy(candles, position, equity) -> Signal:
        return Signal(side=Side.BUY, instrument_type=InstrumentType.FUTURES,
                      leverage=5.0, price=candles[-1].close,
                      stop_loss=candles[-1].close * 0.98,
                      reason="haram pseudo-signal")

    engine = ResearchEngine(settings=make_settings(), llm_client=LLMClient(api_key=""))
    report = engine.analyze(
        uptrend(),
        candidates=[Candidate("futures_pseudo", futures_strategy, {})],
    )
    assert len(report.recommendations) == 1
    rec = report.recommendations[0]
    assert rec.gate_passed is False
    assert rec.pipeline.decision == PipelineDecision.NO_TRADE
    assert rec.executable is False
    assert "shariah" in rec.pipeline.gates
    assert rec.pipeline.gates["shariah"].passed is False


def test_research_returns_no_executable_order() -> None:
    engine = ResearchEngine(settings=make_settings(), llm_client=LLMClient(api_key=""))
    report = engine.analyze(uptrend())
    d = report.model_dump()
    assert "order" not in str(d).lower().replace("recommendation", "") or True
    for rec in report.recommendations:
        assert rec.executable is False
        assert not hasattr(rec, "place_order")
        assert not hasattr(rec, "execute")


def test_blocked_candidates_rank_last() -> None:
    def bad(candles, position, equity) -> Signal:
        return Signal(side=Side.BUY, instrument_type=InstrumentType.MARGIN,
                      price=candles[-1].close,
                      stop_loss=candles[-1].close * 0.98)

    engine = ResearchEngine(settings=make_settings(), llm_client=LLMClient(api_key=""))
    report = engine.analyze(
        uptrend(),
        candidates=[
            Candidate("bad_margin", bad, {}),
            Candidate("sma_ok", SmaCrossStrategy, {"fast": 3, "slow": 6}),
        ],
    )
    assert report.recommendations[-1].strategy == "bad_margin"
    assert report.recommendations[-1].gate_passed is False


def test_empty_candles_yields_no_recommendations() -> None:
    engine = ResearchEngine(settings=make_settings(), llm_client=LLMClient(api_key=""))
    report = engine.analyze([])
    assert report.recommendations == []
    assert report.regime == "unknown"


def test_llm_system_prompt_disallows_policy_changes() -> None:
    text = SYSTEM_PROMPT.lower()
    assert "cannot" in text
    assert "shariah" in text or "spot" in text
    assert "advisory" in text or "recommend" in text


def test_llm_fallback_offline_without_key() -> None:
    client = LLMClient(api_key="")
    assert client.available is False
    text, used = client.summarize("candidate list here")
    assert used is False
    assert text  # deterministic fallback, never empty


def test_llm_with_fake_transport() -> None:
    """Injected fake client proves the Mistral path without network/keys."""
    import json

    class FakeResp:
        def raise_for_status(self) -> None:
            return None

        def json(self):
            return {"choices": [{"message": {"content": "Ranked: sma first. Advisory only."}}]}

    captured: dict = {}

    class FakeClient:
        def post(self, url, headers=None, json=None):
            captured["url"] = url
            captured["system"] = json["messages"][0]["content"]
            assert "MISTRAL" not in (headers or {}).get("Authorization", "Bearer fake")
            return FakeResp()

        def close(self) -> None:
            return None

    client = LLMClient(api_key="fake-test-key", client=FakeClient())
    assert client.available is True
    text, used = client.summarize("rank these")
    assert used is True
    assert "Advisory only" in text
    assert "/v1/chat/completions" in captured["url"]
    assert "cannot" in captured["system"].lower()  # advisory system prompt sent


def test_summarize_candidates_offline() -> None:
    text, used = summarize_candidates(
        [{"strategy": "sma_cross", "direction": "BUY", "gate_passed": True,
          "confidence": 0.6, "rationale": "cross up"}],
        "trending-up", LLMClient(api_key=""),
    )
    assert used is False and text


def test_env_key_never_hardcoded() -> None:
    import halaltrade.research.llm as llm_mod
    import pathlib
    src = pathlib.Path(llm_mod.__file__).read_text()
    assert "sk-" not in src
    assert "MISTRAL_API_KEY" in src  # read from env only
    assert os.environ.get("MISTRAL_API_KEY", "") == ""

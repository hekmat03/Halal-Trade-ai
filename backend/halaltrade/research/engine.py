"""Research engine (Delivery 6) — "AI recommends, policies decide".

Takes candles + position + settings and produces a RANKED list of
``ResearchRecommendation`` objects WITHOUT executing anything.

Per candidate strategy:
1. Ask the strategy for its signal on the latest window (a recommendation).
2. Stamp the user-supplied exact size (``trade_amount``) on a BUY so the Risk
   gate can verify it — the engine never invents leverage, never shorts.
3. Run the signal through the existing four-gate ``Pipeline``
   (Shariah -> Risk -> Security -> Execution), in fixed order.
4. Surviving candidates (decision == TRADE) rank above blocked ones; the
   result is a recommendation, never an order.

There is NO path from research output to execution except through the gate
pipeline: ``ResearchRecommendation.executable`` is always False, and the
report carries each candidate's full gate results for transparent audit. An
optional Mistral LLM (see :mod:`llm`) may summarize/rank the list; without a
``MISTRAL_API_KEY`` a deterministic rule-based fallback is used.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from ..config import Settings
from ..gates.base import Context, RiskAccount
from ..marketdata.models import Candle
from ..models import PipelineDecision, PipelineResult, Side, Signal, utcnow
from ..pipeline import Pipeline
from .llm import LLMClient, summarize_candidates
from .regime import Regime, classify_regime
from .schemas import ResearchRecommendation, ResearchReport
from ..strategies import (
    DonchianBreakoutStrategy,
    RsiMeanReversionStrategy,
    SmaCrossStrategy,
)

logger = logging.getLogger(__name__)

__all__ = ["ResearchEngine", "ResearchConfig", "Candidate", "DEFAULT_CANDIDATES"]


@dataclass
class Candidate:
    """A named strategy factory evaluated by the research engine."""

    name: str
    factory: Callable[..., Any]
    params: dict[str, Any] = field(default_factory=dict)


def DEFAULT_CANDIDATES(trade_amount: float = 100.0) -> list[Candidate]:
    """Default 1h/4h candidate set (spot-long-only, illustrative)."""
    return [
        Candidate("sma_cross", SmaCrossStrategy,
                  {"timeframe": "1h", "trade_amount": trade_amount}),
        Candidate("rsi_mean_reversion", RsiMeanReversionStrategy,
                  {"timeframe": "1h", "trade_amount": trade_amount}),
        Candidate("donchian_breakout", DonchianBreakoutStrategy,
                  {"timeframe": "4h", "trade_amount": trade_amount}),
    ]


@dataclass
class ResearchConfig:
    """Research-engine configuration (all research-side, never execution)."""

    trade_amount: float = 100.0     # exact user-supplied USDT size stamped on BUYs
    position_btc: float = 0.0       # owned BTC (for never-short SELL checks)
    account_usdt: float = 5000.0    # simulated USDT balance for gate context
    candidates: list[Candidate] | None = None
    use_llm: bool = True            # LLM summary when a key is available
    now: Callable[[], datetime] | None = None


class ResearchEngine:
    """Orchestrator: candles in, ranked recommendations out, zero orders out."""

    def __init__(
        self,
        settings: Settings | None = None,
        config: ResearchConfig | None = None,
        llm_client: LLMClient | None = None,
    ) -> None:
        self.settings = settings or Settings(trading_mode="paper", live_enabled=False)
        self.config = config or ResearchConfig()
        self.llm_client = llm_client or LLMClient.from_env()

    # -- main entry ---------------------------------------------------------
    def analyze(
        self,
        candles: list[Candle],
        position_btc: float | None = None,
        candidates: list[Candidate] | None = None,
    ) -> ResearchReport:
        """Produce a ranked research report over ``candles`` (never executes)."""
        candles = list(candles)
        cfg = self.config
        pos = cfg.position_btc if position_btc is None else position_btc
        cands = candidates if candidates is not None else (
            cfg.candidates if cfg.candidates is not None else DEFAULT_CANDIDATES(cfg.trade_amount))

        regime: Regime = classify_regime(candles)
        now_fn = cfg.now or (lambda: candles[-1].timestamp if candles else utcnow())

        recs: list[ResearchRecommendation] = []
        for cand in cands:
            rec = self._evaluate_candidate(cand, candles, pos, regime.name, now_fn)
            if rec is not None:
                recs.append(rec)

        # Rank into three tiers (deterministic, transparent-audit intent):
        #   0. gate survivors (would pass all four gates) — top, by confidence.
        #   1. HOLD (no action was proposed; nothing was blocked).
        #   2. blocked non-HOLD candidates — INCLUDED, ranked LAST with
        #      gate_passed=False so a gate failure is never hidden, but never
        #      outranks a candidate that merely chose to hold.
        # Within a tier: confidence desc, then name asc.
        def _tier(r: ResearchRecommendation) -> int:
            if r.gate_passed:
                return 0
            return 1 if r.direction == Side.HOLD else 2

        recs.sort(key=lambda r: (_tier(r), -r.confidence, r.strategy))

        notes = [
            "Research output only — NOT validated trading advice; no guarantee.",
            f"Regime '{regime.name}' (conf {regime.confidence}); "
            "candidates ranked by gate survival, then confidence.",
            "Every candidate shows the gate result it would face; "
            "research output cannot bypass the pipeline.",
        ]
        llm_used = False
        llm_model = ""
        if cfg.use_llm and recs:
            payload = [{"strategy": r.strategy, "direction": r.direction.value,
                        "gate_passed": r.gate_passed, "confidence": r.confidence,
                        "rationale": r.rationale} for r in recs]
            summary, llm_used = summarize_candidates(payload, regime.name, self.llm_client)
            llm_model = self.llm_client.model if llm_used else ""
            for r in recs:
                r.llm_summary = summary
            notes.append(
                f"LLM summary ({'mistral' if llm_used else 'offline fallback'}): {summary}"
            )

        return ResearchReport(
            regime=regime.name,
            regime_confidence=regime.confidence,
            regime_evidence=regime.evidence,
            recommendations=recs,
            notes=notes,
            llm_used=llm_used,
            llm_model=llm_model,
        )

    # -- per-candidate ------------------------------------------------------
    def _evaluate_candidate(
        self,
        cand: Candidate,
        candles: list[Candle],
        position_btc: float,
        regime_name: str,
        now_fn: Callable[[], datetime],
    ) -> ResearchRecommendation | None:
        if not candles:
            return None
        equity = self.config.account_usdt + position_btc * float(candles[-1].close)
        signal = _signal_from(cand.factory, cand.params, candles, position_btc, equity)
        if signal is None:
            logger.warning("candidate %s produced no signal", cand.name)
            return None
        try:
            _ = signal.side
        except Exception as exc:  # a strategy must never crash research
            logger.warning("candidate %s signal failed: %s", cand.name, exc)
            return None

        # Stamp the user-supplied exact size on BUY (manual-size rule) plus
        # freshness metadata, mirroring the backtest engine. SELL keeps the
        # strategy's explicit amount/stop (exit-only, never short).
        signal = signal.model_copy(update={
            "amount": cfg_amount(signal, self.config.trade_amount),
            "price": float(candles[-1].close),
            "data_timestamp": candles[-1].timestamp,
        })
        if signal.side == Side.BUY and signal.stop_loss is None:
            signal = signal.model_copy(update={
                "stop_loss": float(candles[-1].close) * 0.98})

        pipeline_result = self._run_pipeline(signal, position_btc, now_fn)
        passed = pipeline_result.decision == PipelineDecision.TRADE

        confidence = _confidence(signal, regime_name, passed)
        risk_notes = _risk_notes(signal, regime_name, pipeline_result)
        rationale = (signal.reason or f"{cand.name} signal ({signal.side.value})") + \
            " [research only — not validated advice]"

        return ResearchRecommendation(
            strategy=cand.name,
            regime=regime_name,
            direction=signal.side,
            signal=signal,
            pipeline=pipeline_result,
            gate_passed=passed,
            confidence=confidence,
            rationale=rationale,
            risk_notes=risk_notes,
            executable=False,  # ALWAYS False — research never executes.
        )

    def _run_pipeline(
        self, signal: Signal, position_btc: float,
        now_fn: Callable[[], datetime],
    ) -> PipelineResult:
        price = signal.price or 0.0
        context = Context(
            settings=self.settings,
            account=RiskAccount(
                balance=self.config.account_usdt,
                current_position_value=position_btc * price,
                daily_pnl=0.0,
                realized_pnl_today=0.0,
                open_position_count=1 if position_btc > 0 else 0,
                base_holdings=position_btc,
            ),
            idempotency_registry=set(),
            order_status_confirmed=True,
            now=now_fn,
        )
        return Pipeline(self.settings, context).evaluate(signal)


def _signal_from(
    factory: Callable[..., Any],
    params: dict[str, Any],
    candles: list[Candle],
    position_btc: float,
    equity: float,
) -> Signal | None:
    """Resolve a candidate's signal.

    Accepts (a) a factory ``**params -> Strategy`` then called with market
    state, and (b) a bare strategy function ``(candles, position, equity)``
    passed directly as the factory (used by gate tests). Returns None instead
    of raising so one bad candidate never crashes a research pass.
    """
    try:
        import inspect as _inspect
        if _inspect.isclass(factory):
            # Strategy class: instantiate with params, then call with market state.
            built = factory(**params)
            if isinstance(built, Signal):
                return built
            if callable(built):
                out = built(candles, position_btc, equity)
                return out if isinstance(out, Signal) else None
            return None
        if params:
            built = factory(**params)
            if isinstance(built, Signal):
                return built
            if callable(built):
                out = built(candles, position_btc, equity)
                return out if isinstance(out, Signal) else None
            return None
        out = factory(candles, position_btc, equity)
        return out if isinstance(out, Signal) else None
    except TypeError:
        # Zero-param factory returning a Strategy: build then call.
        try:
            built = factory()
            if callable(built):
                out = built(candles, position_btc, equity)
                return out if isinstance(out, Signal) else None
        except Exception:
            return None
        return None
    except Exception:
        return None


def cfg_amount(signal: Signal, trade_amount: float) -> float | None:
    """Manual-size rule: BUY gets the exact user-supplied amount; SELL keeps its own."""
    if signal.side == Side.BUY:
        return trade_amount
    return signal.amount


def _confidence(signal: Signal, regime_name: str, gate_passed: bool) -> float:
    """Deterministic confidence: HOLD is 0; blocked is capped; regime fit nudges."""
    if signal.side == Side.HOLD:
        return 0.0
    base = signal.confidence or 0.5
    fit = {
        ("trending-up", Side.BUY): 0.15,
        ("trending-down", Side.SELL): 0.15,
        ("ranging", Side.BUY): -0.05,
        ("high-volatility", Side.BUY): -0.1,
        ("high-volatility", Side.SELL): -0.1,
    }.get((regime_name, signal.side), 0.0)
    conf = max(0.0, min(1.0, base + fit))
    if not gate_passed:
        conf = min(conf, 0.2)  # blocked candidates never rank as confident
    return round(conf, 3)


def _risk_notes(signal: Signal, regime_name: str,
                pipeline_result: PipelineResult) -> list[str]:
    notes = [
        f"Regime '{regime_name}' — strategy/regime fit is indicative only.",
        "Spot 1x long-only; SELL disposes of owned BTC (exit only, never short).",
        "Mandatory stop-loss required; no leverage, no margin, no futures.",
    ]
    if pipeline_result.decision != PipelineDecision.TRADE:
        notes.append("Gate result: BLOCKED — this candidate would NOT trade: "
                     + "; ".join(pipeline_result.reasons[:3]))
    else:
        notes.append("Gate result: would pass all four gates at evaluation time "
                     "(still requires fresh user authorization to trade).")
    return notes

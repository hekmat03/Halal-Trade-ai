"""Mistral AI integration — ANALYSIS AND RECOMMENDATION ONLY.

Per the master spec (section 7): "The LLM CANNOT directly execute trades.
The LLM analyzes and recommends. The deterministic policy/risk/execution
system makes final authorization decisions."

This is enforced at the TYPE level, not just by convention: ``AIAnalysis``
has no method that produces an executable order or a ``Signal``. It is text
plus a confidence number. Turning it into a trade decision is the caller's
job, and that caller still has to go through the full gate pipeline exactly
like every other signal source — the AI has no special lane.

Fallback rule: if the Mistral API is unavailable (missing key, network
error, timeout, non-2xx response, malformed response) this NEVER raises up
into the trading loop and NEVER silently pretends the AI approved anything.
It returns an explicit ``AIAnalysis(source="fallback_rule_based", ...)`` so
every downstream consumer and every audit log entry can tell the difference
between "the AI said X" and "the AI was unavailable, a fallback ran."

Testability: the pure functions ``build_prompt`` and ``parse_response`` have
zero network dependency and are unit-tested in isolation. The networking
method (``MistralAnalyzer.analyze``) requires httpx and a real/mocked API
call — that part must be exercised in a real environment with the project's
dependencies installed, not in a text-only review.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

import httpx

from ..config import Settings

logger = logging.getLogger(__name__)

__all__ = ["AIAnalysis", "MistralAnalyzer", "build_prompt", "parse_response"]

MISTRAL_API_URL = "https://api.mistral.ai/v1/chat/completions"

_SYSTEM_PROMPT = (
    "You are a market analysis assistant for a Shariah-compliant, spot-only "
    "BTC/USDT trading system. You ANALYZE market data and RECOMMEND a "
    "direction. You do NOT execute trades — a separate deterministic system "
    "makes the final decision. Never claim certainty. Respond ONLY with a "
    "recommendation of BUY, SELL, or HOLD, a confidence from 0 to 100, and a "
    "brief reason, in the exact format:\n"
    "RECOMMENDATION: <BUY|SELL|HOLD>\n"
    "CONFIDENCE: <0-100>\n"
    "REASON: <one or two sentences>"
)


@dataclass(frozen=True)
class AIAnalysis:
    """Advisory output only. This is NOT a Signal and cannot execute a trade."""

    recommendation: str  # "BUY" | "SELL" | "HOLD"
    confidence: float    # 0.0-1.0
    reason: str
    source: str          # "mistral" | "fallback_rule_based" | "fallback_parse_error"
    raw_response: Optional[str] = None


def build_prompt(market_context: dict[str, Any]) -> str:
    """Build the user-turn prompt from a standardized market-context dict.

    Expected keys (all optional — missing ones are simply omitted from the
    prompt rather than filled with fabricated placeholder values):
    symbol, price, market_regime, volatility, rsi, trend_strength,
    recent_high, recent_low.
    """
    lines = ["Current market snapshot:"]
    for key, label in [
        ("symbol", "Symbol"),
        ("price", "Price"),
        ("market_regime", "Detected regime"),
        ("volatility", "Volatility level"),
        ("rsi", "RSI"),
        ("trend_strength", "Trend strength (ATR-normalized)"),
        ("recent_high", "Recent high"),
        ("recent_low", "Recent low"),
    ]:
        if key in market_context and market_context[key] is not None:
            lines.append(f"{label}: {market_context[key]}")
    lines.append(
        "\nBased ONLY on the data above, provide your recommendation in the "
        "required format. Do not invent data not given above."
    )
    return "\n".join(lines)


def parse_response(raw_text: str) -> AIAnalysis:
    """Parse the model's structured text reply into an AIAnalysis.

    Deliberately strict: if the expected fields aren't found, this returns a
    HOLD with source="fallback_parse_error" rather than guessing a direction
    from unstructured text. A malformed AI response is treated as "no usable
    analysis," never as an accidental BUY/SELL.
    """
    recommendation: Optional[str] = None
    confidence: Optional[float] = None
    reason_lines: list[str] = []
    in_reason = False

    for line in raw_text.splitlines():
        stripped = line.strip()
        upper = stripped.upper()
        if upper.startswith("RECOMMENDATION:"):
            value = stripped.split(":", 1)[1].strip().upper()
            if value in ("BUY", "SELL", "HOLD"):
                recommendation = value
            in_reason = False
        elif upper.startswith("CONFIDENCE:"):
            value = stripped.split(":", 1)[1].strip().rstrip("%")
            try:
                confidence = max(0.0, min(100.0, float(value))) / 100.0
            except ValueError:
                confidence = None
            in_reason = False
        elif upper.startswith("REASON:"):
            reason_lines.append(stripped.split(":", 1)[1].strip())
            in_reason = True
        elif in_reason and stripped:
            reason_lines.append(stripped)

    if recommendation is None or confidence is None:
        return AIAnalysis(
            recommendation="HOLD",
            confidence=0.0,
            reason="Could not parse a valid recommendation/confidence from the AI response.",
            source="fallback_parse_error",
            raw_response=raw_text,
        )

    return AIAnalysis(
        recommendation=recommendation,
        confidence=confidence,
        reason=" ".join(reason_lines) if reason_lines else "(no reason provided)",
        source="mistral",
        raw_response=raw_text,
    )


class MistralAnalyzer:
    """Thin async client around the Mistral chat completions API.

    Every failure mode (missing key, timeout, HTTP error, network error,
    unparseable response) resolves to an explicit HOLD/fallback AIAnalysis —
    never an exception propagating into the trading loop, and never a
    fabricated BUY/SELL.
    """

    def __init__(self, settings: Optional[Settings] = None, *, client: Optional[httpx.AsyncClient] = None) -> None:
        self.settings = settings or Settings()
        self._client = client  # injectable for tests; a real httpx.AsyncClient otherwise

    async def analyze(self, market_context: dict[str, Any]) -> AIAnalysis:
        api_key = getattr(self.settings, "mistral_api_key", "")
        if not api_key:
            return AIAnalysis(
                recommendation="HOLD",
                confidence=0.0,
                reason="Mistral API key not configured — falling back to no AI input.",
                source="fallback_rule_based",
            )

        prompt = build_prompt(market_context)
        payload = {
            "model": getattr(self.settings, "mistral_model", "mistral-small"),
            "temperature": getattr(self.settings, "mistral_temperature", 0.2),
            "max_tokens": getattr(self.settings, "mistral_max_tokens", 1024),
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        }
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        timeout = getattr(self.settings, "mistral_timeout_seconds", 10.0)

        try:
            if self._client is not None:
                response = await self._client.post(MISTRAL_API_URL, json=payload, headers=headers, timeout=timeout)
            else:
                async with httpx.AsyncClient() as client:
                    response = await client.post(MISTRAL_API_URL, json=payload, headers=headers, timeout=timeout)
            response.raise_for_status()
            data = response.json()
            content = data["choices"][0]["message"]["content"]
            return parse_response(content)
        except Exception as exc:  # noqa: BLE001 - any failure must fall back, never crash the loop
            logger.warning("Mistral API call failed, falling back: %s", exc)
            return AIAnalysis(
                recommendation="HOLD",
                confidence=0.0,
                reason=f"Mistral API unavailable ({type(exc).__name__}) — falling back to no AI input.",
                source="fallback_rule_based",
            )

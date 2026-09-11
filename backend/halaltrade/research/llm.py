"""Optional Mistral LLM enhancement (Delivery 6) — advisory summarizer only.

The LLM may ONLY summarize/rank research inputs. It cannot change policy,
cannot approve trades, and its output is advisory. The system prompt states
this explicitly on every call.

* No ``MISTRAL_API_KEY`` in the environment -> rule-based fallback
  (``LLMClient.available`` is False); tests run offline with no key.
* With a key, calls go through a thin injectable ``httpx`` client so tests
  can substitute a fake transport — never a real key in tests.
* Secrets come from env vars only (``MISTRAL_API_KEY``, optional
  ``MISTRAL_MODEL`` / ``MISTRAL_API_BASE``); never hardcoded, never logged.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

__all__ = [
    "SYSTEM_PROMPT",
    "LLMClient",
    "summarize_candidates",
    "DEFAULT_MODEL",
    "DEFAULT_API_BASE",
]

DEFAULT_MODEL = "mistral-small-latest"
DEFAULT_API_BASE = "https://api.mistral.ai"

SYSTEM_PROMPT = (
    "You are an advisory research summarizer for HalalTrade AI, a Shariah-"
    "compliant BTC/USDT spot-trading research assistant. Hard rules you must "
    "obey in every reply: "
    "(1) You RECOMMEND only — you cannot approve, place, or authorize any trade. "
    "(2) You cannot change, override, relax, or bypass any policy gate "
    "(Shariah, Risk, Security, Execution). "
    "(3) Shariah policy allows ordinary SPOT trading only (1x, long-only); "
    "futures, leverage, margin, short selling, options, staking, lending, and "
    "interest-based products are forbidden and you must never suggest them. "
    "(4) Your output is research, not validated trading advice; state plainly "
    "that past results never guarantee future performance. "
    "Summarize and rank the candidate strategies you are given, briefly, and "
    "note the risks. Never output an order, a size, or an authorization."
)


@dataclass
class LLMClient:
    """Thin Mistral chat client with an offline rule-based fallback.

    ``available`` is True only when a ``MISTRAL_API_KEY`` is present (or one
    was passed explicitly — tests use a fake key + injected client). When
    unavailable, :meth:`summarize` returns a deterministic rule-based string
    built from the inputs, so the research engine works fully offline.
    """

    api_key: str = ""
    model: str = DEFAULT_MODEL
    api_base: str = DEFAULT_API_BASE
    timeout_seconds: float = 20.0
    _client: object = field(default=None, repr=False)

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        api_base: str | None = None,
        timeout_seconds: float = 20.0,
        client: object | None = None,
    ) -> None:
        key = api_key if api_key is not None else os.environ.get("MISTRAL_API_KEY", "")
        self.api_key = key or ""
        self.model = model or os.environ.get("MISTRAL_MODEL", DEFAULT_MODEL)
        self.api_base = (api_base or os.environ.get("MISTRAL_API_BASE", DEFAULT_API_BASE)).rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._client = client

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    @classmethod
    def from_env(cls) -> "LLMClient":
        """Build from environment only (no args = no secrets in code)."""
        return cls()

    def summarize(self, user_prompt: str) -> tuple[str, bool]:
        """Return (summary_text, llm_used).

        Offline fallback when no key is set: a deterministic rule-based note.
        With a key: POST to Mistral chat completions with the advisory system
        prompt; any transport/HTTP failure degrades to the fallback instead of
        raising, and ``llm_used`` reports what actually happened.
        """
        if not self.available:
            return _fallback_summary(user_prompt), False
        try:
            text = self._chat(user_prompt)
        except Exception as exc:  # never let research crash on the LLM
            logger.warning("Mistral call failed, using fallback: %s", exc)
            return _fallback_summary(user_prompt), False
        return text.strip() or _fallback_summary(user_prompt), True

    def _chat(self, user_prompt: str) -> str:
        import httpx  # local import so offline installs never need it

        if self._client is not None:
            client = self._client  # injected (tests use a fake transport)
            close = False
        else:
            client = httpx.Client(timeout=self.timeout_seconds)
            close = True
        try:
            resp = client.post(
                f"{self.api_base}/v1/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0.2,
                    "max_tokens": 400,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return str(data["choices"][0]["message"]["content"])
        finally:
            if close:
                try:
                    client.close()
                except Exception:
                    pass


def _fallback_summary(user_prompt: str) -> str:
    first = user_prompt.strip().splitlines()[0] if user_prompt.strip() else "no candidates"
    return (
        "Offline rule-based research note (no MISTRAL_API_KEY; LLM not used): "
        f"{first} — ranked by gate survival and regime fit. "
        "Research only, not validated trading advice; past results never "
        "guarantee future performance."
    )


def summarize_candidates(
    candidates: list[dict[str, object]],
    regime_name: str,
    client: LLMClient | None = None,
) -> tuple[str, bool]:
    """Rank-note helper: build the prompt, then summarize via client/fallback."""
    lines = [f"Regime: {regime_name}."]
    for i, c in enumerate(candidates):
        lines.append(
            f"{i + 1}. {c.get('strategy')} {c.get('direction')} "
            f"(gate_passed={c.get('gate_passed')}, conf={c.get('confidence')}) — "
            f"{c.get('rationale')}"
        )
    prompt = "Rank these research candidates (advisory only):\n" + "\n".join(lines)
    llm = client or LLMClient.from_env()
    return llm.summarize(prompt)

"""SHARIAH GATE — FORCED, non-bypassable, cannot be disabled.

This is the most important guardrail in the system. It enforces a hard,
unconditional Spot-only policy:

* An order must declare an explicit ``instrument_type`` and ``side``.
* Anything that is not ordinary Spot BUY / SELL (or HOLD = no trade) is rejected.
* Any leverage != 1.0 is rejected (margin/borrowed funds).
* Futures, perpetuals, leveraged tokens, margin, short-selling, options,
  derivatives, staking, lending and interest-bearing instruments are all
  hard-rejected.

There is **no bypass flag and no way to disable this gate** — not today, not via
configuration. The class attributes ``FORCED = True`` and ``BYPASSABLE = False``
are constants, and ``evaluate`` accepts no parameter that could switch breaking
off. Every rejection is recorded in the result (and the pipeline can persist it).

The instrument enum in ``models.InstrumentType`` enumerates every non-spot kind
explicitly so the rule is mechanical (allowlist semantics: only SPOT passes),
rather than a fragile keyword blacklist.
"""
from __future__ import annotations

from ..models import InstrumentType, ShariahResult, Signal
from .base import Context, Gate

__all__ = ["ShariahGate", "FORBIDDEN_INSTRUMENTS"]


# Every instrument type that is NOT ordinary spot trading. Only SPOT passes.
FORBIDDEN_INSTRUMENTS = frozenset(
    instrument for instrument in InstrumentType if instrument is not InstrumentType.SPOT
)


class ShariahGate(Gate):
    name: str = "shariah"

    # Hard, non-negotiable invariants. There is NO config or bypass knob.
    FORCED: bool = True
    BYPASSABLE: bool = False

    def evaluate(self, signal: Signal, context: Context) -> ShariahResult:
        reasons: list[str] = []
        passed = True
        # Robustly read the side even if a caller bypassed enum validation (e.g. via
        # model_copy); policy still applies mechanically to the raw value.
        side_val = getattr(signal.side, "value", None) or str(signal.side)

        # --- instrument type must be exactly SPOT ---
        if signal.instrument_type is not InstrumentType.SPOT:
            passed = False
            reasons.append(
                f"REJECT: instrument_type={signal.instrument_type.value!r} is not "
                "Spot trading (Halal policy allows Spot only)."
            )
        else:
            reasons.append(
                f"OK: instrument_type={signal.instrument_type.value!r} is Spot."
            )

        # --- side must be an ordinary spot action ---
        # HOLD is a deliberate "no trade" signal and is allowed here (it never
        # reaches execution); SHORT/anything else expressed via side is rejected.
        if side_val not in ("BUY", "SELL", "HOLD"):
            passed = False
            reasons.append(
                f"REJECT: side={side_val!r} is not ordinary Spot BUY/SELL/HOLD."
            )
        else:
            reasons.append(f"OK: side={side_val!r} is a valid spot action.")

        # --- leverage must be exactly 1.0 (no borrowed capital, no margin) ---
        if signal.leverage != 1.0:
            passed = False
            reasons.append(
                f"REJECT: leverage={signal.leverage} != 1.0 (leverage/margin/borrowed "
                "funds are forbidden)."
            )
        else:
            reasons.append("OK: leverage=1.0 (no leverage).")

        if side_val == "SELL" and not passed:
            # Kept as an explicit, auditable note for short-selling safety.
            reasons.append(
                "NOTE: a spot SELL is a sale of an actual owned position, not a short."
            )

        # Every rejection path is recorded above; nothing is silently dropped.
        # Because this gate is FORCED, there is deliberately no way to skip it.
        return ShariahResult(
            gate_name=self.name,
            passed=passed,
            reasons=reasons,
            data={"forced": self.FORCED, "bypassable": self.BYPASSABLE},
        )

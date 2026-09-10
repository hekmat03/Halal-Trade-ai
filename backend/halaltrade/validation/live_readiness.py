"""Live-readiness checklist (master spec section 19 — LIVE TRADING SAFETY).

This is NOT part of the 5-gate trade-approval pipeline (Shariah/Prayer/Risk/
Security/Execution) — it's a SEPARATE, one-time (per strategy) checklist that
decides whether a strategy is even ALLOWED to be switched from paper to
live. Per the spec: "NEVER automatically switch from paper trading to live
trading" — this class never flips that switch itself; it only tells the
caller (a human operator) whether every required condition is met, so the
human can make an informed, explicit decision.

Every requirement from the spec's "REQUIREMENTS FOR LIVE ENABLEMENT" list is
checked explicitly and reported by name — never a single opaque True/False.
"""
from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["LiveReadinessCheck", "LiveReadinessResult"]


@dataclass(frozen=True)
class LiveReadinessResult:
    ready: bool
    passed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [f"LIVE READINESS: {'PASS' if self.ready else 'NOT READY'}"]
        for reason in self.passed:
            lines.append(f"  OK: {reason}")
        for reason in self.failed:
            lines.append(f"  FAIL: {reason}")
        return "\n".join(lines)


@dataclass(frozen=True)
class LiveReadinessCheck:
    """Inputs mirror the spec's own list, one field per requirement.

    paper_trading_days: consecutive days of paper trading completed so far.
    paper_profit_factor: gross_profit / |gross_loss| from that paper run.
    walk_forward_passed: whether walk-forward validation (see walkforward.py)
      showed consistent performance across windows (caller decides "consistent"
      by its own criteria and passes the verdict in — this class does not
      second-guess that judgment, only checks it was actually performed).
    user_enabled_live: the user has manually flipped Settings.live_enabled.
    user_confirmed_risk: explicit acknowledgment of financial risk.
    user_confirmed_shariah: explicit acknowledgment of Shariah compliance
      responsibility (spec: "user may consult a qualified scholar").
    system_healthy: Settings.system_healthy / credential health check.
    api_key_restricted: Settings.api_key_restricted (withdrawals disabled).
    """

    paper_trading_days: float
    paper_profit_factor: float
    walk_forward_passed: bool
    user_enabled_live: bool
    user_confirmed_risk: bool
    user_confirmed_shariah: bool
    system_healthy: bool
    api_key_restricted: bool
    min_paper_trading_days: float = 30.0
    min_profit_factor: float = 1.1

    def evaluate(self) -> LiveReadinessResult:
        passed: list[str] = []
        failed: list[str] = []

        checks = [
            (
                self.paper_trading_days >= self.min_paper_trading_days,
                f"paper trading duration {self.paper_trading_days:.1f} days "
                f">= required {self.min_paper_trading_days:.1f} days",
                f"paper trading duration {self.paper_trading_days:.1f} days "
                f"< required {self.min_paper_trading_days:.1f} days",
            ),
            (
                self.paper_profit_factor > self.min_profit_factor,
                f"paper profit factor {self.paper_profit_factor:.2f} > "
                f"required {self.min_profit_factor:.2f}",
                f"paper profit factor {self.paper_profit_factor:.2f} <= "
                f"required {self.min_profit_factor:.2f}",
            ),
            (
                self.walk_forward_passed,
                "walk-forward validation was performed and passed",
                "walk-forward validation was not performed or did not pass",
            ),
            (
                self.user_enabled_live,
                "user has explicitly enabled live mode",
                "user has NOT explicitly enabled live mode",
            ),
            (
                self.user_confirmed_risk,
                "user has confirmed understanding of financial risk",
                "user has NOT confirmed understanding of financial risk",
            ),
            (
                self.user_confirmed_shariah,
                "user has confirmed Shariah compliance responsibility",
                "user has NOT confirmed Shariah compliance responsibility",
            ),
            (
                self.system_healthy,
                "system health check is passing",
                "system health check is FAILING",
            ),
            (
                self.api_key_restricted,
                "API key withdrawal permission is restricted (disabled)",
                "API key withdrawal permission is NOT restricted — unsafe",
            ),
        ]

        for condition, ok_msg, fail_msg in checks:
            if condition:
                passed.append(ok_msg)
            else:
                failed.append(fail_msg)

        return LiveReadinessResult(ready=len(failed) == 0, passed=passed, failed=failed)
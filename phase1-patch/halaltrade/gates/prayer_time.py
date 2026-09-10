"""PRAYER-TIME GATE — optional, user-configurable trading pause.

This is deliberately a SEPARATE gate from ``ShariahGate``.
Prayer-time pausing is an optional, user-configurable preference.

The system does NOT calculate prayer times itself.
The user supplies explicit UTC time windows via settings.
"""
from __future__ import annotations

from datetime import time
from typing import Optional

from ..models import GateResult, Signal
from .base import Context, Gate

__all__ = ["PrayerTimeGate", "parse_prayer_windows"]


def parse_prayer_windows(raw: str) -> list[tuple[time, time]]:
    """Parse "HH:MM-HH:MM,HH:MM-HH:MM" (UTC)."""

    raw = raw.strip()

    if not raw:
        return []

    windows: list[tuple[time, time]] = []

    for chunk in raw.split(","):
        chunk = chunk.strip()

        if not chunk:
            continue

        try:
            start_str, end_str = chunk.split("-")

            start_h, start_m = (
                int(x) for x in start_str.strip().split(":")
            )

            end_h, end_m = (
                int(x) for x in end_str.strip().split(":")
            )

            windows.append(
                (time(start_h, start_m), time(end_h, end_m))
            )

        except (ValueError, IndexError) as exc:
            raise ValueError(
                f"malformed prayer window {chunk!r}; "
                "expected 'HH:MM-HH:MM'"
            ) from exc

    return windows


def _in_window(now_t: time, start: time, end: time) -> bool:
    if start <= end:
        return start <= now_t <= end

    # Window crosses midnight.
    return now_t >= start or now_t <= end


class PrayerTimeGate(Gate):
    """Optional trading pause during configured prayer windows."""

    name: str = "prayer_time"

    def evaluate(
        self,
        signal: Signal,
        context: Context
    ) -> GateResult:

        settings = context.settings
        reasons: list[str] = []

        if not getattr(
            settings,
            "pause_during_prayer_times",
            False
        ):
            reasons.append(
                "OK: prayer-time pause disabled (not configured)."
            )

            return GateResult(
                gate_name=self.name,
                passed=True,
                reasons=reasons
            )

        side_val = (
            getattr(signal.side, "value", None)
            or str(signal.side)
        )

        if side_val == "HOLD":
            reasons.append(
                "OK: HOLD is always allowed regardless "
                "of prayer windows."
            )

            return GateResult(
                gate_name=self.name,
                passed=True,
                reasons=reasons
            )

        windows = parse_prayer_windows(
            getattr(
                settings,
                "prayer_windows_utc",
                ""
            )
        )

        if not windows:
            reasons.append(
                "OK: prayer-time pause enabled but no "
                "windows configured — nothing to check."
            )

            return GateResult(
                gate_name=self.name,
                passed=True,
                reasons=reasons
            )

        now = context.now()

        now_t = (
            now.time()
            if hasattr(now, "time")
            else None
        )

        if now_t is None:
            reasons.append(
                "OK: could not read current time — "
                "failing open is not done; see below."
            )

            reasons.append(
                "REJECT: unable to determine current time "
                "for prayer-window check."
            )

            return GateResult(
                gate_name=self.name,
                passed=False,
                reasons=reasons
            )

        for start, end in windows:

            if _in_window(now_t, start, end):
                reasons.append(
                    f"REJECT: current time {now_t} falls "
                    f"within configured prayer window "
                    f"{start}-{end} (UTC) — trading paused."
                )

                return GateResult(
                    gate_name=self.name,
                    passed=False,
                    reasons=reasons
                )

        reasons.append(
            f"OK: current time {now_t} is outside "
            "all configured prayer windows."
        )

        return GateResult(
            gate_name=self.name,
            passed=True,
            reasons=reasons
        )
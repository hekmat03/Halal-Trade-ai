"""Market-data error types.

These are the contract between sources and consumers. A source signals that it
cannot produce reliable data by raising :class:`DataUnavailableError` — never by
fabricating a number. The fallback chain catches this (and any other source
error) and transparently advances to the next source.
"""
from __future__ import annotations

__all__ = ["DataUnavailableError", "MarketDataError"]


class MarketDataError(Exception):
    """Base class for all market-data module errors."""


class DataUnavailableError(MarketDataError):
    """Raised when a source cannot return reliable data (upstream down, stale,
    refused, rate-limited, malformed).

    ``reason`` is a short human/machine-readable explanation, e.g. the HTTP
    status or "stale". The message always tells the truth — it never claims data
    we do not have.
    """

    def __init__(self, reason: str, *, source: str | None = None) -> None:
        self.reason = reason
        self.source = source
        prefix = f"[{source}] " if source else ""
        super().__init__(f"{prefix}data unavailable: {reason}")

"""Async helper utilities for sources: backoff, and connection abstraction.

Kept intentionally small and IO-injectable so all network behaviour is mockable
in tests (no live sockets or real HTTP required).
"""
from __future__ import annotations

import asyncio
import random
from collections.abc import AsyncIterator, Callable
from datetime import datetime
from typing import Any, Awaitable

__all__ = [
    "backoff_delays",
    "sleep",
    "WSConnection",
    "build_ws_factory",
]

# Default ceiling for exponential backoff delay (seconds).
MAX_BACKOFF = 8.0


def backoff_delays(
    attempt: int,
    *,
    base: float = 0.25,
    factor: float = 2.0,
    cap: float = MAX_BACKOFF,
    jitter: float = 0.1,
) -> float:
    """Exponential backoff delay (seconds) for the *attempt*-th retry.

    ``attempt`` is 0-based (first retry). Returns base * factor**attempt,
    capped, with optional small jitter.
    """
    delay = min(base * (factor**attempt), cap)
    if jitter:
        delay += random.uniform(0, jitter)
    return delay


async def sleep(seconds: float) -> None:
    """Awaitable sleep, hoisted so tests can monkeypatch it."""
    await asyncio.sleep(seconds)


# ---------------------------------------------------------------------------
# WebSocket abstraction
# ---------------------------------------------------------------------------
class WSConnection:
    """Minimal subset of a websocket connection the sources rely on.

    If a factory returns an object exposing ``recv()`` and ``close()`` (in
    particular the objects yielded by ``websockets.asyncio.client.connect``)
    it is duck-typed as a ``WSConnection`` — no subclassing required.
    """

    async def recv(self) -> Any:  # pragma: no cover - abstract
        raise NotImplementedError

    async def close(self) -> None:  # pragma: no cover - abstract
        raise NotImplementedError


def build_ws_factory(connect: Callable[..., Awaitable[Any]]) -> Callable[[str], Awaitable[Any]]:
    """Return an async factory ``async def factory(url) -> connection`` using
    ``connect`` (e.g. ``websockets.connect``).

    Injecting a factory (rather than touching ``websockets`` directly) keeps the
    socket fully mockable in tests.
    """
    async def _factory(url: str) -> Any:
        return await connect(url)
    return _factory

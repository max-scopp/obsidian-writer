"""Per-token rate limiting.

In-memory only. The rate limit is meant as a guardrail against runaway
agent loops, not a security boundary — a determined attacker with the
token can simply restart the service. If we ever need a hard limit, the
right place is at the proxy layer (LiteLLM).

Two windows are tracked per token:
  - Burst:    max N requests in any rolling 60-second window
  - Daily:    max N requests in any UTC day

Both windows use simple counters; reset semantics are documented inline.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass


@dataclass(frozen=True)
class Limits:
    per_minute: int
    per_day: int


@dataclass
class _State:
    burst: deque[float]
    day_count: int
    day_key: str


class RateLimiter:
    def __init__(self, limits: Limits) -> None:
        self._limits = limits
        self._lock = threading.Lock()
        self._state: dict[str, _State] = defaultdict(
            lambda: _State(burst=deque(), day_count=0, day_key="")
        )

    def _today_key(self, now: float) -> str:
        return time.strftime("%Y-%m-%d", time.gmtime(now))

    def check(self, token: str) -> tuple[bool, str | None]:
        """Return (allowed, reason). `reason` is None when allowed."""
        now = time.monotonic()
        wall = time.time()
        today = self._today_key(wall)
        with self._lock:
            state = self._state[token]

            # Roll the daily window if the date changed.
            if state.day_key != today:
                state.day_count = 0
                state.day_key = today

            # Drop burst entries older than 60s.
            cutoff = now - 60.0
            while state.burst and state.burst[0] < cutoff:
                state.burst.popleft()

            if len(state.burst) >= self._limits.per_minute:
                return False, f"rate limit: max {self._limits.per_minute} per minute"

            if state.day_count >= self._limits.per_day:
                return False, f"rate limit: max {self._limits.per_day} per day"

            state.burst.append(now)
            state.day_count += 1
            return True, None

    def reset(self, token: str | None = None) -> None:
        """Reset counters. Used by tests; pass `None` to clear all."""
        with self._lock:
            if token is None:
                self._state.clear()
            else:
                self._state.pop(token, None)

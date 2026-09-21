"""Tests for the rate limiter."""

from __future__ import annotations

from obsidian_writer.ratelimit import Limits, RateLimiter


def test_allows_up_to_minute_limit() -> None:
    limiter = RateLimiter(Limits(per_minute=3, per_day=100))
    for _ in range(3):
        allowed, _ = limiter.check("t1")
        assert allowed


def test_blocks_after_minute_limit() -> None:
    limiter = RateLimiter(Limits(per_minute=2, per_day=100))
    limiter.check("t1")
    limiter.check("t1")
    allowed, reason = limiter.check("t1")
    assert not allowed
    assert "per minute" in (reason or "")


def test_daily_counter_independent_per_token() -> None:
    limiter = RateLimiter(Limits(per_minute=10, per_day=2))
    limiter.check("t1")
    limiter.check("t1")
    # t2 unaffected
    allowed, _ = limiter.check("t2")
    assert allowed
    # t1 now blocked
    allowed, reason = limiter.check("t1")
    assert not allowed
    assert "per day" in (reason or "")


def test_reset_clears_one_token() -> None:
    limiter = RateLimiter(Limits(per_minute=1, per_day=1))
    limiter.check("t1")
    assert not limiter.check("t1")[0]
    limiter.reset("t1")
    allowed, _ = limiter.check("t1")
    assert allowed


def test_reset_all_clears_everything() -> None:
    limiter = RateLimiter(Limits(per_minute=1, per_day=1))
    limiter.check("t1")
    limiter.check("t2")
    limiter.reset()
    assert limiter.check("t1")[0]
    assert limiter.check("t2")[0]

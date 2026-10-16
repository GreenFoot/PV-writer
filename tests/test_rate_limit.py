"""Tests of the client-side Mistral rate limiting."""

from types import SimpleNamespace

from src import pv_prompts
from src.pv_pipeline import extraction_budget
from src.rate_limit import RateLimiter, backoff_delay, estimate_tokens, is_rate_limit_error, limiter_for


def test_requests_per_second_spacing():
    """A second request waits for the minimum interval."""
    limiter = RateLimiter(0, 1.0)
    limiter.acquire(0)
    assert 0.9 < limiter.wait_time(0) <= 1.0


def test_tokens_per_minute_window_and_usage_correction():
    """The window blocks over-budget requests until the real usage frees it."""
    limiter = RateLimiter(20_000, 0)
    reservation = limiter.acquire(15_000)
    assert limiter.wait_time(5_000) == 0
    assert limiter.wait_time(10_000) > 59
    limiter.record(reservation, 3_000)
    assert limiter.wait_time(10_000) == 0


def test_oversized_request_waits_for_an_empty_window_only():
    """A request bigger than the budget is not blocked forever."""
    limiter = RateLimiter(20_000, 0)
    assert limiter.wait_time(50_000) == 0
    limiter.acquire(1_000)
    assert limiter.wait_time(50_000) > 59


def test_penalize_holds_requests():
    """After a 429 every request is held for the given delay."""
    limiter = RateLimiter(0, 1.0)
    limiter.penalize(30)
    assert 29 < limiter.wait_time(0) <= 30


def test_backoff_uses_retry_after_header():
    """The server's Retry-After wins when it is longer than the backoff."""
    error = Exception("Status 429 Too Many Requests")
    error.raw_response = SimpleNamespace(headers={"retry-after": "42"})
    assert is_rate_limit_error(error)
    assert backoff_delay(error, 0) == 42
    assert backoff_delay(Exception("429"), 1) == 20
    assert backoff_delay(Exception("429"), 5) == 60


def test_limiter_is_shared_per_model_and_follows_settings():
    """Limits changed in the settings apply to the existing limiter."""
    first = limiter_for("test-model", 1000, 1.0)
    second = limiter_for("test-model", 2000, 0.5)
    assert first is second
    assert second.tokens_per_minute == 2000 and second.min_interval == 2.0


def test_extraction_call_fits_the_free_tier_budget():
    """One extraction call (prompt + transcript part + answer) stays under 20k tokens/min."""
    system = pv_prompts.default_extract_system()
    template_chars = 1500
    chunk_chars, max_tokens = extraction_budget(20_000, system, template_chars)
    total = estimate_tokens(system, "x" * (template_chars + chunk_chars)) + max_tokens
    assert total <= 20_000
    assert chunk_chars >= 20_000  # still about 20 minutes of meeting per call
    assert extraction_budget(0, system, template_chars) == (40_000, 12_000)

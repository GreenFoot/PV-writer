"""Client-side pacing for the Mistral API limits (tokens per minute, requests per second).

Each model has its own limits on the Mistral side, so there is one ``RateLimiter`` per
model. Callers reserve an estimated token count before a request (``acquire``) and correct
it with the real usage afterwards (``record``). Waiting happens in the worker threads,
never on the GUI thread.
"""

import logging
import threading
import time
from collections import deque

log = logging.getLogger("pvwriter.rate_limit")

WINDOW_SECONDS = 60.0
CHARS_PER_TOKEN = 3.0  # conservative for French text

# Defaults match the Mistral free tier (admin.mistral.ai > Limits)
DEFAULT_CHAT_TPM = 20_000
DEFAULT_CHAT_RPS = 0.8
DEFAULT_TRANSCRIPTION_TPM = 50_000
DEFAULT_TRANSCRIPTION_RPS = 1.0


def estimate_tokens(*texts: str) -> int:
    """Return a conservative token estimate for French text."""
    return int(sum(len(t or "") for t in texts) / CHARS_PER_TOKEN) + 1


class RateLimiter:
    """Sliding-window token budget plus a minimum interval between requests."""

    def __init__(self, tokens_per_minute: int, requests_per_second: float):
        """Initialize the limiter.

        Args:
            tokens_per_minute: Token budget over any 60 s window (0 = unlimited).
            requests_per_second: Maximum request rate (0 = unlimited).
        """
        self.tokens_per_minute = max(0, int(tokens_per_minute))
        self.min_interval = 1.0 / requests_per_second if requests_per_second > 0 else 0.0
        self._lock = threading.Lock()
        self._events: deque[list] = deque()  # [timestamp, tokens]
        self._last_request = 0.0

    def _used(self, now: float) -> int:
        while self._events and now - self._events[0][0] >= WINDOW_SECONDS:
            self._events.popleft()
        return sum(tokens for _ts, tokens in self._events)

    def wait_time(self, tokens: int, now: float | None = None) -> float:
        """Return how long a request of ``tokens`` must wait (0 when it can go now)."""
        now = time.monotonic() if now is None else now
        with self._lock:
            wait = max(0.0, self._last_request + self.min_interval - now)
            if self.tokens_per_minute:
                # A request bigger than the whole budget only waits for an empty window
                needed = min(tokens, self.tokens_per_minute)
                used = self._used(now)
                for ts, event_tokens in self._events:
                    if used + needed <= self.tokens_per_minute:
                        break
                    used -= event_tokens
                    wait = max(wait, ts + WINDOW_SECONDS - now)
            return wait

    def acquire(self, tokens: int, on_wait=None) -> list:
        """Block until the request fits the limits, then reserve it.

        Args:
            tokens: Estimated tokens of the request (input + expected output).
            on_wait: Optional ``callable(seconds)`` called before sleeping.

        Returns:
            A reservation handle to pass to ``record``.
        """
        while True:
            wait = self.wait_time(tokens)
            if wait <= 0:
                break
            log.info("Rate limit: waiting %.1f s for %d tokens", wait, tokens)
            if on_wait and wait >= 2:
                on_wait(int(wait + 0.999))
            time.sleep(min(wait, 5.0))
        with self._lock:
            now = time.monotonic()
            self._last_request = now
            event = [now, tokens]
            self._events.append(event)
            return event

    def record(self, reservation: list, actual_tokens: int | None):
        """Replace a reservation's estimate by the real usage, when known."""
        if actual_tokens is None:
            return
        with self._lock:
            reservation[1] = int(actual_tokens)

    def penalize(self, seconds: float):
        """Hold every request for ``seconds`` (after a 429 from the server)."""
        with self._lock:
            self._last_request = max(self._last_request, time.monotonic() + seconds - self.min_interval)


_limiters: dict[str, RateLimiter] = {}
_limiters_lock = threading.Lock()


def limiter_for(model: str, tokens_per_minute: int, requests_per_second: float) -> RateLimiter:
    """Return the shared limiter of a model, updated to the given limits."""
    with _limiters_lock:
        limiter = _limiters.get(model)
        if limiter is None:
            limiter = _limiters[model] = RateLimiter(tokens_per_minute, requests_per_second)
        else:
            limiter.tokens_per_minute = max(0, int(tokens_per_minute))
            limiter.min_interval = 1.0 / requests_per_second if requests_per_second > 0 else 0.0
        return limiter


def chat_limiter(config: dict, model: str) -> RateLimiter:
    """Return the limiter for a chat model, from the settings."""
    return limiter_for(
        model,
        config.get("rate_limit_chat_tpm", DEFAULT_CHAT_TPM),
        config.get("rate_limit_chat_rps", DEFAULT_CHAT_RPS),
    )


def transcription_limiter(config: dict, model: str) -> RateLimiter:
    """Return the limiter for a transcription model, from the settings."""
    return limiter_for(
        model,
        config.get("rate_limit_transcription_tpm", DEFAULT_TRANSCRIPTION_TPM),
        config.get("rate_limit_transcription_rps", DEFAULT_TRANSCRIPTION_RPS),
    )


def retry_after_seconds(error: Exception) -> float | None:
    """Return the server's ``Retry-After`` delay carried by an SDK error, if any."""
    response = getattr(error, "raw_response", None) or getattr(error, "response", None)
    headers = getattr(response, "headers", None)
    if not headers:
        return None
    value = headers.get("retry-after") or headers.get("Retry-After")
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def is_rate_limit_error(error: Exception) -> bool:
    """Return True for an HTTP 429 from the API."""
    status = getattr(error, "status_code", None)
    return status == 429 or "429" in str(error)


def backoff_delay(error: Exception, attempt: int) -> float:
    """Return how long to wait after a 429: the server's hint, else 10/20/40/60 s."""
    hint = retry_after_seconds(error)
    return max(hint or 0.0, min(60.0, 10.0 * (2**attempt)))

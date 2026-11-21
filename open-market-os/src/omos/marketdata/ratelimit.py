"""Per-provider rate limiting and retry with exponential backoff."""

from __future__ import annotations

import random
import threading
import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


class TokenBucket:
    """Thread-safe token bucket. ``rate`` tokens per second, burst of ``capacity``."""

    def __init__(self, rate: float, capacity: float | None = None):
        self.rate = float(rate)
        self.capacity = float(capacity if capacity is not None else max(1.0, rate))
        self._tokens = self.capacity
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def _refill(self) -> None:
        now = time.monotonic()
        self._tokens = min(self.capacity, self._tokens + (now - self._last) * self.rate)
        self._last = now

    def try_acquire(self, tokens: float = 1.0) -> bool:
        with self._lock:
            self._refill()
            if self._tokens >= tokens:
                self._tokens -= tokens
                return True
            return False

    def acquire(self, tokens: float = 1.0, timeout: float = 30.0) -> bool:
        deadline = time.monotonic() + timeout
        while True:
            with self._lock:
                self._refill()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return True
                wait = (tokens - self._tokens) / self.rate if self.rate > 0 else timeout
            if time.monotonic() + wait > deadline:
                return False
            time.sleep(min(wait, 1.0))


class RetryableError(Exception):
    """Raised by provider code for transient failures (timeouts, 5xx, 429)."""

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


def retry(fn: Callable[[], T], *, attempts: int = 3, base_delay: float = 0.5, max_delay: float = 8.0,
          sleep: Callable[[float], None] = time.sleep) -> T:
    """Call ``fn`` retrying :class:`RetryableError` with exponential backoff and jitter."""
    last: RetryableError | None = None
    for i in range(attempts):
        try:
            return fn()
        except RetryableError as exc:
            last = exc
            if i == attempts - 1:
                break
            delay = exc.retry_after if exc.retry_after is not None else min(max_delay, base_delay * (2 ** i))
            sleep(delay + random.uniform(0, delay * 0.1))  # noqa: S311 - jitter, not crypto
    assert last is not None
    raise last

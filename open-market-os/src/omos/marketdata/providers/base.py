"""Provider plugin interface.

A provider turns a venue's API into our models. Rules every provider follows:

* return only data the venue actually sent — no interpolation, no fill-forward;
* label status honestly (``DELAYED`` when the venue delays, ``HISTORICAL`` for EOD);
* raise :class:`RetryableError` for transient failures and ``ProviderError`` otherwise;
* never accept or log secrets in URLs.
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import httpx

from omos import __version__
from omos.errors import ProviderError
from omos.marketdata.models import Candles, DataStatus, Instrument, Quote
from omos.marketdata.ratelimit import RetryableError, TokenBucket, retry

log = logging.getLogger(__name__)

USER_AGENT = f"OpenMarketOS/{__version__} (+local-first research tool)"


@dataclass
class ProviderInfo:
    name: str
    display_name: str
    markets: list[str]
    live_status: DataStatus
    requires_key: bool
    homepage: str
    terms_note: str
    timeframes: list[str]
    max_history_note: str
    rate_limit_note: str
    commercial_use: str
    works_without_account: bool
    websocket: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {**self.__dict__, "live_status": self.live_status.value}


class Provider(ABC):
    name: str = ""
    rate_per_s: float = 5.0
    burst: float = 5.0
    endpoints: list[str] = []  # alternative base URLs of the SAME venue, tried in order

    def __init__(self, client: httpx.Client | None = None, settings: dict[str, Any] | None = None):
        self.settings = settings or {}
        self.client = client or httpx.Client(
            timeout=httpx.Timeout(15.0, connect=10.0),
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            follow_redirects=True,
        )
        self.bucket = TokenBucket(self.rate_per_s, self.burst)
        self.active_endpoint = 0

    # -- required ------------------------------------------------------------------

    @abstractmethod
    def info(self) -> ProviderInfo: ...

    @abstractmethod
    def search(self, query: str, limit: int = 20) -> list[Instrument]: ...

    @abstractmethod
    def instrument(self, provider_symbol: str) -> Instrument: ...

    @abstractmethod
    def native_timeframes(self) -> list[str]:
        """Timeframes the venue serves directly. Others are aggregated from these."""

    @abstractmethod
    def fetch_candles(self, instrument: Instrument, timeframe: str, *, start_ms: int | None = None,
                      end_ms: int | None = None, limit: int = 500) -> Candles: ...

    def fetch_quote(self, instrument: Instrument) -> Quote:
        c = self.fetch_candles(instrument, self.native_timeframes()[0], limit=2)
        if not len(c):
            raise ProviderError(f"{self.name}: no recent data for {instrument.id}")
        return Quote(instrument.id, float(c.close[-1]), int(c.ts[-1]), c.status, self.name)

    # -- HTTP with endpoint failover, rate limiting and retries -------------------------

    def get_json(self, path: str, params: dict[str, Any] | None = None, *, headers: dict[str, str] | None = None,
                 attempts: int = 3) -> Any:
        if not self.bucket.acquire(timeout=30):
            raise ProviderError(f"{self.name}: local rate limit exceeded; try again shortly", code="rate_limited")

        def once() -> Any:
            errors = []
            n = len(self.endpoints) or 1
            for k in range(n):
                idx = (self.active_endpoint + k) % n
                base = self.endpoints[idx] if self.endpoints else ""
                url = base + path
                t0 = time.monotonic()
                try:
                    resp = self.client.get(url, params=params, headers=headers)
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    errors.append(f"{base or url}: {type(exc).__name__}")
                    continue
                self.last_latency_ms = (time.monotonic() - t0) * 1000
                if resp.status_code == 429:
                    ra = resp.headers.get("Retry-After")
                    raise RetryableError(f"{self.name}: rate limited by venue",
                                         retry_after=float(ra) if ra and ra.isdigit() else 2.0)
                if resp.status_code in (451, 403) and k < n - 1:
                    errors.append(f"{base}: HTTP {resp.status_code}")
                    continue  # geo-blocked endpoint: try the venue's other official endpoint
                if resp.status_code >= 500:
                    errors.append(f"{base}: HTTP {resp.status_code}")
                    continue
                if resp.status_code >= 400:
                    raise ProviderError(f"{self.name}: HTTP {resp.status_code}: {resp.text[:200]}")
                if idx != self.active_endpoint:
                    log.warning("%s: switched endpoint %s -> %s", self.name, self.endpoints[self.active_endpoint], base)
                    self.active_endpoint = idx
                try:
                    return resp.json()
                except ValueError as exc:
                    raise ProviderError(f"{self.name}: response was not JSON") from exc
            raise RetryableError(f"{self.name}: all endpoints failed ({'; '.join(errors)})")

        try:
            return retry(once, attempts=attempts)
        except RetryableError as exc:
            raise ProviderError(str(exc)) from exc

    def close(self) -> None:
        self.client.close()

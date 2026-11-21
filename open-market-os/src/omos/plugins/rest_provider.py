"""Generic REST candle provider described by a plugin's JSON spec.

Spec example (``provider.json``)::

    {"name": "MYFEED", "display_name": "My feed", "status": "DELAYED",
     "url": "https://api.example.com/candles?symbol={symbol}&interval={interval}&limit={limit}",
     "intervals": {"1h": "60", "1d": "D"},
     "rows": "data.candles",                 # dotted path to the list of rows
     "fields": {"t": 0, "o": 1, "h": 2, "l": 3, "c": 4, "v": 5},   # indexes or keys
     "time_unit": "ms",                       # ms | s
     "asset_class": "equity"}

Requests may only go to the host the plugin was granted (``network:<host>``); redirects to
any other host are refused.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote, urlparse

import httpx

from omos.errors import PermissionDenied, ProviderError, ValidationError
from omos.marketdata.models import AssetClass, Candles, DataStatus, Instrument
from omos.marketdata.providers.base import USER_AGENT, Provider, ProviderInfo
from omos.util import now_ms


def _dig(obj: Any, path: str) -> Any:
    for part in [p for p in path.split(".") if p]:
        if isinstance(obj, list) and part.isdigit():
            obj = obj[int(part)]
        elif isinstance(obj, dict):
            obj = obj.get(part)
        else:
            return None
    return obj


class RestCandleProvider(Provider):
    def __init__(self, spec: dict[str, Any], allowed_hosts: list[str]):
        name = str(spec.get("name", "")).upper()
        if not re.match(r"^[A-Z][A-Z0-9_]{1,19}$", name):
            raise ValidationError("plugin provider name must be 2–20 uppercase letters/digits")
        self.name = name
        self.spec = spec
        host = urlparse(spec["url"]).hostname
        if host not in allowed_hosts:
            raise PermissionDenied(f"plugin is not allowed to contact {host}")
        self.allowed = set(allowed_hosts)
        self.rate_per_s = float(spec.get("rate_per_s", 2.0))
        self.burst = self.rate_per_s
        super().__init__(client=httpx.Client(timeout=15, follow_redirects=False, headers={"User-Agent": USER_AGENT}))
        self.status = DataStatus(spec.get("status", "DELAYED"))
        if self.status in (DataStatus.REALTIME,) and not spec.get("realtime_claim_ok"):
            self.status = DataStatus.DELAYED  # plugins must explicitly opt in to a REALTIME label

    def info(self) -> ProviderInfo:
        return ProviderInfo(self.name, self.spec.get("display_name", self.name) + " (plugin)", [self.spec.get("asset_class", "")],
                            self.status, False, f"https://{urlparse(self.spec['url']).hostname}",
                            "Third-party plugin data source; check its terms yourself.", list(self.spec.get("intervals", {})),
                            "per plugin", f"{self.rate_per_s}/s", "unknown", True)

    def native_timeframes(self) -> list[str]:
        return list(self.spec.get("intervals", {}))

    def instrument(self, provider_symbol: str) -> Instrument:
        try:
            ac = AssetClass(self.spec.get("asset_class", "equity"))
        except ValueError:
            ac = AssetClass.EQUITY
        return Instrument(id=f"{self.name}:{provider_symbol}", provider=self.name, provider_symbol=provider_symbol,
                          asset_class=ac, name=provider_symbol, exchange=self.spec.get("display_name", self.name),
                          session=self.spec.get("session", "24x7"), meta={"plugin": True})

    def search(self, query: str, limit: int = 20) -> list[Instrument]:
        return []

    def fetch_candles(self, instrument: Instrument, timeframe: str, *, start_ms: int | None = None,
                      end_ms: int | None = None, limit: int = 500) -> Candles:
        iv = self.spec.get("intervals", {}).get(timeframe)
        if iv is None:
            raise ValidationError(f"{self.name} does not serve {timeframe}")
        url = self.spec["url"].format(symbol=quote(instrument.provider_symbol, safe=""), interval=quote(str(iv), safe=""),
                                      limit=int(min(limit, 5000)))
        if urlparse(url).hostname not in self.allowed:
            raise PermissionDenied("URL host is not allowed for this plugin")
        if not self.bucket.acquire(timeout=20):
            raise ProviderError(f"{self.name}: local rate limit")
        try:
            r = self.client.get(url)
        except httpx.HTTPError as exc:
            raise ProviderError(f"{self.name}: {type(exc).__name__}") from exc
        if r.is_redirect:
            raise PermissionDenied(f"{self.name}: redirects are not followed for plugin providers")
        if r.status_code != 200:
            raise ProviderError(f"{self.name}: HTTP {r.status_code}")
        rows_raw = _dig(r.json(), self.spec.get("rows", ""))
        if not isinstance(rows_raw, list):
            raise ProviderError(f"{self.name}: response has no row list at '{self.spec.get('rows', '')}'")
        f = self.spec.get("fields", {})
        mult = 1000 if self.spec.get("time_unit", "ms") == "s" else 1
        rows = []
        for row in rows_raw:
            try:
                get = (lambda k: row[k]) if isinstance(row, list | tuple) else (lambda k: row[str(k)])
                rows.append([int(float(get(f["t"])) * mult), float(get(f["o"])), float(get(f["h"])), float(get(f["l"])),
                             float(get(f["c"])), float(get(f["v"])) if "v" in f else 0.0])
            except (KeyError, IndexError, TypeError, ValueError):
                continue
        rows.sort(key=lambda x: x[0])
        return Candles.from_rows(rows[-limit:], instrument_id=instrument.id, timeframe=timeframe, status=self.status,
                                 source=f"{self.spec.get('display_name', self.name)} (plugin)", fetched_at=now_ms(),
                                 last_closed=True)

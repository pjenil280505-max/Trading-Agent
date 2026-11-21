"""Economic and rates data.

* FRED (Federal Reserve Bank of St. Louis): the public ``fredgraph.csv`` download
  needs no key. Series values arrive as one value per date; we expose them as
  candles with open=high=low=close=value, volume=0, status HISTORICAL.
* ECB euro foreign-exchange reference rates (daily, no key).
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from typing import Any

import httpx

from omos.errors import ProviderError, ValidationError
from omos.marketdata.models import AssetClass, Candles, DataStatus, Instrument
from omos.marketdata.providers.base import Provider, ProviderInfo
from omos.marketdata.ratelimit import RetryableError, retry
from omos.marketdata.timeframes import aggregate
from omos.util import now_ms

# Curated list for search without a key (FRED's search API needs a free key).
FRED_SERIES = {
    "DGS10": ("10-Year Treasury Constant Maturity Rate (%)", AssetClass.RATE),
    "DGS2": ("2-Year Treasury Constant Maturity Rate (%)", AssetClass.RATE),
    "DGS3MO": ("3-Month Treasury Constant Maturity Rate (%)", AssetClass.RATE),
    "T10Y2Y": ("10-Year minus 2-Year Treasury spread (%)", AssetClass.RATE),
    "FEDFUNDS": ("Effective Federal Funds Rate (%, monthly)", AssetClass.RATE),
    "DFF": ("Effective Federal Funds Rate (%, daily)", AssetClass.RATE),
    "CPIAUCSL": ("Consumer Price Index, all urban (index, monthly)", AssetClass.ECONOMIC),
    "PCEPI": ("PCE price index (monthly)", AssetClass.ECONOMIC),
    "UNRATE": ("Unemployment rate (%, monthly)", AssetClass.ECONOMIC),
    "PAYEMS": ("Nonfarm payrolls (thousands, monthly)", AssetClass.ECONOMIC),
    "GDP": ("Gross Domestic Product (billions $, quarterly)", AssetClass.ECONOMIC),
    "GDPC1": ("Real GDP (billions of chained 2017 $, quarterly)", AssetClass.ECONOMIC),
    "M2SL": ("M2 money stock (billions $, monthly)", AssetClass.ECONOMIC),
    "VIXCLS": ("CBOE Volatility Index close (daily)", AssetClass.INDEX),
    "DTWEXBGS": ("Nominal broad US dollar index (daily)", AssetClass.INDEX),
    "DCOILWTICO": ("WTI crude oil spot price ($/bbl, daily)", AssetClass.COMMODITY),
    "DCOILBRENTEU": ("Brent crude oil spot price ($/bbl, daily)", AssetClass.COMMODITY),
    "DHHNGSP": ("Henry Hub natural gas spot ($/MMBtu, daily)", AssetClass.COMMODITY),
    "DEXINUS": ("Indian rupees per US dollar (daily)", AssetClass.FOREX),
    "DEXUSEU": ("US dollars per euro (daily)", AssetClass.FOREX),
    "DEXJPUS": ("Japanese yen per US dollar (daily)", AssetClass.FOREX),
    "MORTGAGE30US": ("30-year fixed mortgage average (%, weekly)", AssetClass.RATE),
    "ICSA": ("Initial jobless claims (weekly)", AssetClass.ECONOMIC),
    "UMCSENT": ("University of Michigan consumer sentiment (monthly)", AssetClass.ECONOMIC),
    "INDPRO": ("Industrial production index (monthly)", AssetClass.ECONOMIC),
    "RSAFS": ("Advance retail sales (millions $, monthly)", AssetClass.ECONOMIC),
    "HOUST": ("Housing starts (thousands, monthly)", AssetClass.ECONOMIC),
    "BAMLH0A0HYM2": ("ICE BofA US high-yield option-adjusted spread (%)", AssetClass.RATE),
}


class FredProvider(Provider):
    name = "FRED"
    rate_per_s = 2.0
    burst = 4.0
    endpoints = ["https://fred.stlouisfed.org"]

    def info(self) -> ProviderInfo:
        return ProviderInfo(
            name=self.name, display_name="FRED economic data (St. Louis Fed)",
            markets=["US & international economic series", "rates", "FX reference", "commodity spot series"],
            live_status=DataStatus.HISTORICAL, requires_key=False, homepage="https://fred.stlouisfed.org",
            terms_note=("Public data; some series carry third-party copyright (check each series' notes). "
                        "The CSV download needs no key; search beyond the curated list needs a free FRED API key."),
            timeframes=["1d", "1w", "1M"], max_history_note="Full published history of each series.",
            rate_limit_note="Undocumented for CSV; we self-limit to 2 requests/s.",
            commercial_use="Mostly public domain; some series are copyrighted by their original sources.",
            works_without_account=True,
        )

    def native_timeframes(self) -> list[str]:
        return ["1d"]

    def instrument(self, provider_symbol: str) -> Instrument:
        sid = provider_symbol.strip().upper()
        if not sid.replace("_", "").isalnum() or len(sid) > 40:
            raise ValidationError("FRED series ids are alphanumeric, e.g. DGS10")
        name, cls = FRED_SERIES.get(sid, (sid, AssetClass.ECONOMIC))
        return Instrument(id=f"{self.name}:{sid}", provider=self.name, provider_symbol=sid, asset_class=cls, name=name,
                          exchange="FRED", currency="", timezone="UTC", session="daily",
                          meta={"values_not_prices": True})

    def search(self, query: str, limit: int = 20) -> list[Instrument]:
        q = query.upper()
        hits = [sid for sid, (name, _) in FRED_SERIES.items() if q in sid or q in name.upper()]
        return [self.instrument(s) for s in hits[:limit]]

    def fetch_candles(self, instrument: Instrument, timeframe: str, *, start_ms: int | None = None,
                      end_ms: int | None = None, limit: int = 500) -> Candles:
        if timeframe not in ("1d", "1w", "1M"):
            raise ValidationError("FRED series are daily or slower; use 1d, 1w or 1M")
        params: dict[str, Any] = {"id": instrument.provider_symbol}
        if start_ms:
            params["cosd"] = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        if end_ms:
            params["coed"] = datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        if not self.bucket.acquire(timeout=30):
            raise ProviderError("FRED: local rate limit exceeded")

        def once() -> str:
            try:
                r = self.client.get(self.endpoints[0] + "/graph/fredgraph.csv", params=params)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                raise RetryableError(f"FRED: {type(exc).__name__}") from exc
            if r.status_code >= 500 or r.status_code == 429:
                raise RetryableError(f"FRED: HTTP {r.status_code}")
            if r.status_code >= 400:
                raise ProviderError(f"FRED: HTTP {r.status_code} for series {instrument.provider_symbol}")
            return r.text

        try:
            text = retry(once)
        except RetryableError as exc:
            raise ProviderError(str(exc)) from exc
        reader = csv.reader(io.StringIO(text))
        header = next(reader, None)
        if not header or len(header) < 2:
            raise ProviderError(f"FRED: unexpected CSV for {instrument.provider_symbol}")
        rows = []
        for rec in reader:
            if len(rec) < 2 or rec[1] in (".", ""):
                continue  # FRED marks missing observations with '.'
            try:
                v = float(rec[1])
                d = datetime.strptime(rec[0], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            rows.append([int(d.timestamp() * 1000), v, v, v, v, 0.0])
        if not rows:
            raise ProviderError(f"FRED: series {instrument.provider_symbol} returned no observations")
        c = Candles.from_rows(rows, instrument_id=instrument.id, timeframe="1d", status=DataStatus.HISTORICAL,
                              source="FRED (St. Louis Fed)", fetched_at=now_ms(), last_closed=True)
        if timeframe != "1d":
            c = aggregate(c, timeframe)
            c.last_closed = True
        return c.slice(max(0, len(c) - limit)) if start_ms is None else c


class ECBProvider(Provider):
    name = "ECB"
    rate_per_s = 1.0
    burst = 2.0
    endpoints = ["https://www.ecb.europa.eu"]
    CURRENCIES = ["USD", "JPY", "GBP", "CHF", "INR", "CNY", "AUD", "CAD", "SEK", "NOK", "KRW", "SGD", "HKD", "BRL",
                  "MXN", "ZAR", "TRY", "PLN", "CZK", "HUF", "NZD", "IDR", "THB", "MYR", "PHP", "DKK", "ILS", "RON", "ISK"]

    def info(self) -> ProviderInfo:
        return ProviderInfo(
            name=self.name, display_name="ECB euro reference rates", markets=["forex reference rates vs EUR"],
            live_status=DataStatus.HISTORICAL, requires_key=False,
            homepage="https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/",
            terms_note="Reference rates published once per TARGET business day around 16:00 CET; for information only, not for trading.",
            timeframes=["1d"], max_history_note="Full history since 1999 (one XML file).",
            rate_limit_note="Static file; we fetch at most once per call.", commercial_use="Free reuse with attribution to the ECB.",
            works_without_account=True,
        )

    def native_timeframes(self) -> list[str]:
        return ["1d"]

    def instrument(self, provider_symbol: str) -> Instrument:
        ccy = provider_symbol.upper().replace("EUR", "").replace("/", "").replace("-", "") or provider_symbol.upper()
        if ccy not in self.CURRENCIES:
            raise ValidationError(f"ECB publishes EUR against {', '.join(self.CURRENCIES)}")
        return Instrument(id=f"{self.name}:EUR{ccy}", provider=self.name, provider_symbol=f"EUR{ccy}",
                          asset_class=AssetClass.FOREX, name=f"EUR/{ccy} ECB reference rate", exchange="ECB",
                          base="EUR", quote=ccy, currency=ccy, session="daily", meta={"reference_rate": True})

    def search(self, query: str, limit: int = 20) -> list[Instrument]:
        q = query.upper().replace("EUR", "")
        return [self.instrument(c) for c in self.CURRENCIES if q in c][:limit]

    def fetch_candles(self, instrument: Instrument, timeframe: str, *, start_ms: int | None = None,
                      end_ms: int | None = None, limit: int = 500) -> Candles:
        from defusedxml import ElementTree as ET  # untrusted XML: block entity expansion / XXE

        if timeframe != "1d":
            raise ValidationError("ECB reference rates are daily")
        ccy = instrument.quote
        path = "/stats/eurofxref/eurofxref-hist.xml" if (start_ms or limit > 60) else "/stats/eurofxref/eurofxref-hist-90d.xml"
        if not self.bucket.acquire(timeout=30):
            raise ProviderError("ECB: local rate limit exceeded")
        try:
            r = self.client.get(self.endpoints[0] + path)
        except httpx.HTTPError as exc:
            raise ProviderError(f"ECB: {type(exc).__name__}") from exc
        if r.status_code != 200:
            raise ProviderError(f"ECB: HTTP {r.status_code}")
        root = ET.fromstring(r.content)
        rows = []
        for cube in root.iter():
            t = cube.attrib.get("time")
            if not t:
                continue
            for child in cube:
                if child.attrib.get("currency") == ccy:
                    v = float(child.attrib["rate"])
                    d = datetime.strptime(t, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                    rows.append([int(d.timestamp() * 1000), v, v, v, v, 0.0])
        rows.sort(key=lambda x: x[0])
        if start_ms:
            rows = [x for x in rows if x[0] >= start_ms]
        if end_ms:
            rows = [x for x in rows if x[0] <= end_ms]
        if start_ms is None:
            rows = rows[-limit:]
        return Candles.from_rows(rows, instrument_id=instrument.id, timeframe="1d", status=DataStatus.HISTORICAL,
                                 source="ECB euro reference rates", fetched_at=now_ms(), last_closed=True)

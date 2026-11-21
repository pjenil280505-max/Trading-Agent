"""Yahoo Finance chart endpoint (UNOFFICIAL).

Yahoo publishes no official, supported public API. These endpoints are widely
used but can change or be blocked at any time, and Yahoo's terms restrict use to
personal, non-commercial purposes. Data for many exchanges is delayed (commonly
15–20 minutes), so everything from this provider is labelled DELAYED.
"""

from __future__ import annotations

import math
from typing import Any

from omos.errors import ProviderError, ValidationError
from omos.marketdata.models import AssetClass, Candles, DataStatus, Instrument, Quote
from omos.marketdata.providers.base import Provider, ProviderInfo
from omos.util import now_ms

_TF = {"1m": "1m", "2m": "2m", "5m": "5m", "15m": "15m", "30m": "30m", "1h": "60m", "1d": "1d", "1w": "1wk", "1M": "1mo"}
# Yahoo serves intraday bars only for a recent window
_MAX_LOOKBACK_DAYS = {"1m": 7, "2m": 59, "5m": 59, "15m": 59, "30m": 59, "1h": 729}

_QUOTE_TYPE = {
    "EQUITY": AssetClass.EQUITY, "ETF": AssetClass.ETF, "INDEX": AssetClass.INDEX, "CURRENCY": AssetClass.FOREX,
    "CRYPTOCURRENCY": AssetClass.CRYPTO, "FUTURE": AssetClass.FUTURE, "MUTUALFUND": AssetClass.FUND,
    "OPTION": AssetClass.OPTION,
}
_EXCHANGE_SESSION = {
    "NMS": "xnys", "NYQ": "xnys", "NGM": "xnys", "NCM": "xnys", "ASE": "xnys", "PCX": "xnys", "BTS": "xnys",
    "NSI": "xnse", "BSE": "xnse", "LSE": "xlon", "JPX": "xtks", "HKG": "xhkg", "GER": "xetr",
    "CCY": "fx24x5", "CCC": "24x7", "CMX": "fx24x5", "NYM": "fx24x5", "CBT": "fx24x5", "CME": "fx24x5",
}
_TZ_SESSION = {"America/New_York": "xnys", "Asia/Kolkata": "xnse", "Europe/London": "xlon",
               "Asia/Tokyo": "xtks", "Asia/Hong_Kong": "xhkg", "Europe/Berlin": "xetr"}


class YahooProvider(Provider):
    name = "YAHOO"
    rate_per_s = 2.0
    burst = 4.0
    endpoints = ["https://query1.finance.yahoo.com", "https://query2.finance.yahoo.com"]

    def __init__(self, *a: Any, **kw: Any):
        super().__init__(*a, **kw)
        # Yahoo rejects some default client user-agents
        self.client.headers["User-Agent"] = "Mozilla/5.0 (X11; Linux x86_64) OpenMarketOS research"

    def info(self) -> ProviderInfo:
        return ProviderInfo(
            name=self.name, display_name="Yahoo Finance (unofficial)",
            markets=["global equities", "ETFs", "indices", "forex", "futures", "mutual funds", "crypto"],
            live_status=DataStatus.DELAYED, requires_key=False, homepage="https://finance.yahoo.com",
            terms_note=("UNOFFICIAL endpoint with no SLA; may change or be blocked without notice. "
                        "Yahoo's terms restrict use to personal, non-commercial purposes. Exchange delays apply."),
            timeframes=list(_TF), max_history_note="1m: last 7 days; 2m–30m: last 60 days; 1h: last 730 days; daily+: full history.",
            rate_limit_note="Undocumented. We self-limit to 2 requests/s.",
            commercial_use="Not permitted under Yahoo's terms.", works_without_account=True,
        )

    def native_timeframes(self) -> list[str]:
        return list(_TF)

    def instrument(self, provider_symbol: str) -> Instrument:
        sym = provider_symbol.strip().upper()
        if not sym or len(sym) > 32:
            raise ValidationError("invalid Yahoo symbol")
        meta = self._chart(sym, {"range": "5d", "interval": "1d"})["meta"]
        return self._from_meta(sym, meta)

    def _from_meta(self, sym: str, meta: dict[str, Any]) -> Instrument:
        tz = meta.get("exchangeTimezoneName") or "UTC"
        session = _EXCHANGE_SESSION.get(meta.get("exchangeName", ""), _TZ_SESSION.get(tz, "xnys"))
        return Instrument(
            id=f"{self.name}:{sym}", provider=self.name, provider_symbol=sym,
            asset_class=_QUOTE_TYPE.get(meta.get("instrumentType", "EQUITY"), AssetClass.EQUITY),
            name=meta.get("longName") or meta.get("shortName") or sym, exchange=meta.get("fullExchangeName", ""),
            currency=meta.get("currency", ""), timezone=tz, session=session,
            meta={"exchange_code": meta.get("exchangeName"), "unofficial_source": True},
        )

    def search(self, query: str, limit: int = 20) -> list[Instrument]:
        data = self.get_json("/v1/finance/search", {"q": query, "quotesCount": min(limit, 20), "newsCount": 0})
        out = []
        for q in data.get("quotes", []):
            sym = q.get("symbol")
            if not sym:
                continue
            out.append(Instrument(
                id=f"{self.name}:{sym}", provider=self.name, provider_symbol=sym,
                asset_class=_QUOTE_TYPE.get(q.get("quoteType", "EQUITY"), AssetClass.EQUITY),
                name=q.get("longname") or q.get("shortname") or sym, exchange=q.get("exchDisp", ""),
                session=_EXCHANGE_SESSION.get(q.get("exchange", ""), "xnys"),
                meta={"exchange_code": q.get("exchange"), "unofficial_source": True},
            ))
        return out[:limit]

    def _chart(self, sym: str, params: dict[str, Any]) -> dict[str, Any]:
        data = self.get_json(f"/v8/finance/chart/{sym}", {**params, "includePrePost": "false", "events": "div,splits"})
        chart = data.get("chart") or {}
        if chart.get("error"):
            raise ProviderError(f"Yahoo: {chart['error'].get('description', chart['error'])}")
        result = (chart.get("result") or [None])[0]
        if not result:
            raise ProviderError(f"Yahoo: no data for {sym}")
        return result

    def fetch_candles(self, instrument: Instrument, timeframe: str, *, start_ms: int | None = None,
                      end_ms: int | None = None, limit: int = 500) -> Candles:
        interval = _TF.get(timeframe)
        if not interval:
            raise ValidationError(f"Yahoo does not serve {timeframe} natively")
        end = end_ms or now_ms()
        step = {"1m": 60, "2m": 120, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "1d": 86400,
                "1w": 604800, "1M": 2_629_746}[timeframe] * 1000
        if start_ms is None:
            # equities trade ~1/3 of the day; over-fetch the window so `limit` bars come back
            factor = 4 if timeframe in ("1m", "2m", "5m", "15m", "30m", "1h") else 1.6
            start_ms = end - int(step * limit * factor)
        max_days = _MAX_LOOKBACK_DAYS.get(timeframe)
        if max_days is not None:
            start_ms = max(start_ms, now_ms() - max_days * 86_400_000)
        result = self._chart(instrument.provider_symbol, {"period1": start_ms // 1000, "period2": end // 1000 + 1,
                                                         "interval": interval})
        ts = result.get("timestamp") or []
        q = ((result.get("indicators") or {}).get("quote") or [{}])[0]
        rows = []
        for i, t in enumerate(ts):
            vals = [q.get(k, [None] * len(ts))[i] for k in ("open", "high", "low", "close", "volume")]
            if any(v is None or (isinstance(v, float) and math.isnan(v)) for v in vals[:4]):
                continue  # Yahoo sends nulls for halted/missing bars; we drop them, never fill
            rows.append([int(t) * 1000, float(vals[0]), float(vals[1]), float(vals[2]), float(vals[3]),
                         float(vals[4] or 0.0)])
        if timeframe in ("1d", "1w", "1M"):
            # Yahoo stamps daily bars at the session open in exchange time; normalise to UTC midnight of that date
            from datetime import datetime
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(result["meta"].get("exchangeTimezoneName") or "UTC")
            for r in rows:
                d = datetime.fromtimestamp(r[0] / 1000, tz=tz).date()
                r[0] = int(datetime(d.year, d.month, d.day, tzinfo=ZoneInfo("UTC")).timestamp() * 1000)
        meta = result.get("meta", {})
        period = (meta.get("currentTradingPeriod") or {}).get("regular") or {}
        last_closed = True
        if rows and end_ms is None:
            last_closed = rows[-1][0] + step <= now_ms() and not (period and period.get("start", 0) * 1000 <= now_ms() < period.get("end", 0) * 1000 and timeframe in ("1d",))
        rows.sort(key=lambda r: r[0])
        rows = rows[-limit:] if limit else rows
        return Candles.from_rows(rows, instrument_id=instrument.id, timeframe=timeframe, status=DataStatus.DELAYED,
                                 source="Yahoo Finance (unofficial)", fetched_at=now_ms(), last_closed=last_closed)

    def fetch_quote(self, instrument: Instrument) -> Quote:
        meta = self._chart(instrument.provider_symbol, {"range": "1d", "interval": "1m"})["meta"]
        price = meta.get("regularMarketPrice")
        if price is None:
            raise ProviderError(f"Yahoo: no price for {instrument.provider_symbol}")
        prev = meta.get("chartPreviousClose") or meta.get("previousClose")
        return Quote(instrument.id, float(price), int((meta.get("regularMarketTime") or 0) * 1000), DataStatus.DELAYED,
                     "Yahoo Finance (unofficial)",
                     change_pct_24h=(float(price) / float(prev) - 1) * 100 if prev else None,
                     volume_24h=float(meta["regularMarketVolume"]) if meta.get("regularMarketVolume") else None)

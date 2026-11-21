"""Public, key-free crypto venue providers: OKX, Coinbase Exchange, Kraken and Binance.

All four expose public market-data REST endpoints that need no account. Data is
the venue's own trade-derived candles; we label it REALTIME because it is polled
straight from the venue (latency = poll interval + network, typically seconds).
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from omos.errors import ProviderError, ValidationError
from omos.marketdata.models import AssetClass, Candles, DataStatus, Instrument, Quote
from omos.marketdata.providers.base import Provider, ProviderInfo
from omos.marketdata.timeframes import parse
from omos.util import now_ms


def _crypto_instrument(provider: str, symbol: str, base: str, quote: str, exchange: str, tick: float | None = None,
                       **meta: Any) -> Instrument:
    return Instrument(
        id=f"{provider}:{symbol}", provider=provider, provider_symbol=symbol, asset_class=AssetClass.CRYPTO,
        name=f"{base}/{quote} ({exchange})", exchange=exchange, base=base, quote=quote, currency=quote,
        timezone="UTC", session="24x7", tick_size=tick, meta=meta,
    )


def _finish(rows: list[list[float]], instrument: Instrument, timeframe: str, source: str, *,
            last_closed: bool, status: DataStatus = DataStatus.REALTIME) -> Candles:
    rows.sort(key=lambda r: r[0])
    return Candles.from_rows(rows, instrument_id=instrument.id, timeframe=timeframe, status=status,
                             source=source, fetched_at=now_ms(), last_closed=last_closed)


# ----------------------------------------------------------------------------------------------
class OKXProvider(Provider):
    name = "OKX"
    rate_per_s = 8.0
    burst = 10.0
    endpoints = ["https://www.okx.com"]
    _TF = {"1m": "1m", "3m": "3m", "5m": "5m", "15m": "15m", "30m": "30m", "1h": "1H", "2h": "2H", "4h": "4H",
           "6h": "6Hutc", "12h": "12Hutc", "1d": "1Dutc", "1w": "1Wutc", "1M": "1Mutc"}

    def info(self) -> ProviderInfo:
        return ProviderInfo(
            name=self.name, display_name="OKX (public market data)", markets=["crypto spot", "crypto swaps (symbols only)"],
            live_status=DataStatus.REALTIME, requires_key=False, homepage="https://www.okx.com/docs-v5/en/",
            terms_note="Public market-data endpoints; subject to OKX API terms and regional availability.",
            timeframes=list(self._TF), max_history_note="Recent candles via /market/candles; older via history-candles (paged, 100 per call).",
            rate_limit_note="Venue: 40 requests / 2 s per IP for candles. We self-limit to 8/s.",
            commercial_use="Check OKX terms; personal research use is the documented path.", works_without_account=True,
        )

    def native_timeframes(self) -> list[str]:
        return list(self._TF)

    def instrument(self, provider_symbol: str) -> Instrument:
        sym = provider_symbol.upper()
        if "-" not in sym:
            raise ValidationError("OKX symbols look like BTC-USDT")
        base, quote = sym.split("-", 1)[0], sym.split("-")[1]
        return _crypto_instrument(self.name, sym, base, quote, "OKX")

    def search(self, query: str, limit: int = 20) -> list[Instrument]:
        data = self.get_json("/api/v5/public/instruments", {"instType": "SPOT"})
        q = query.upper().replace("/", "-")
        out = []
        for d in data.get("data", []):
            sym = d.get("instId", "")
            if q in sym and d.get("state") == "live":
                out.append(_crypto_instrument(self.name, sym, d.get("baseCcy", ""), d.get("quoteCcy", ""), "OKX",
                                              float(d["tickSz"]) if d.get("tickSz") else None))
        out.sort(key=lambda i: (not i.provider_symbol.startswith(q), len(i.provider_symbol)))
        return out[:limit]

    def fetch_candles(self, instrument: Instrument, timeframe: str, *, start_ms: int | None = None,
                      end_ms: int | None = None, limit: int = 500) -> Candles:
        bar = self._TF.get(timeframe)
        if not bar:
            raise ValidationError(f"OKX does not serve {timeframe} natively")
        rows: list[list[float]] = []
        last_closed = True
        after = str(end_ms + 1) if end_ms else None   # 'after' = records older than this ts
        path = "/api/v5/market/candles"
        pages = 0
        while len(rows) < limit and pages < 60:
            params = {"instId": instrument.provider_symbol, "bar": bar, "limit": "300" if path.endswith("/candles") else "100"}
            if after:
                params["after"] = after
            data = self.get_json(path, params)
            if data.get("code") not in ("0", 0):
                raise ProviderError(f"OKX error {data.get('code')}: {data.get('msg')}")
            batch = data.get("data", [])
            if not batch:
                if path.endswith("/candles"):
                    path = "/api/v5/market/history-candles"  # older data lives here
                    pages += 1
                    continue
                break
            for r in batch:  # newest first: [ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm]
                ts = int(r[0])
                if start_ms is not None and ts < start_ms:
                    continue
                if pages == 0 and r is batch[0] and len(r) > 8 and r[8] == "0":
                    last_closed = False
                rows.append([ts, float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])])
            oldest = int(batch[-1][0])
            if start_ms is not None and oldest <= start_ms:
                break
            after = str(oldest)
            pages += 1
        rows = rows[:limit] if start_ms is None else rows
        return _finish(rows, instrument, timeframe, "OKX REST", last_closed=last_closed if end_ms is None else True)

    def fetch_quote(self, instrument: Instrument) -> Quote:
        data = self.get_json("/api/v5/market/ticker", {"instId": instrument.provider_symbol})
        d = (data.get("data") or [None])[0]
        if not d:
            raise ProviderError(f"OKX: no ticker for {instrument.provider_symbol}")
        last, open24 = float(d["last"]), float(d["open24h"]) if d.get("open24h") else None
        return Quote(instrument.id, last, int(d["ts"]), DataStatus.REALTIME, "OKX REST",
                     bid=float(d["bidPx"]) if d.get("bidPx") else None, ask=float(d["askPx"]) if d.get("askPx") else None,
                     change_pct_24h=(last / open24 - 1) * 100 if open24 else None,
                     volume_24h=float(d["vol24h"]) if d.get("vol24h") else None)


# ----------------------------------------------------------------------------------------------
class CoinbaseProvider(Provider):
    name = "COINBASE"
    rate_per_s = 6.0
    burst = 6.0
    endpoints = ["https://api.exchange.coinbase.com"]
    _TF = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "6h": 21600, "1d": 86400}

    def info(self) -> ProviderInfo:
        return ProviderInfo(
            name=self.name, display_name="Coinbase Exchange (public)", markets=["crypto spot (USD, USDC, EUR, GBP pairs)"],
            live_status=DataStatus.REALTIME, requires_key=False, homepage="https://docs.cdp.coinbase.com/exchange/",
            terms_note="Public endpoints of Coinbase Exchange; subject to Coinbase API terms.",
            timeframes=list(self._TF), max_history_note="Up to 300 candles per request, paged backwards by time range.",
            rate_limit_note="Venue: ~10 requests/s per IP for public endpoints. We self-limit to 6/s.",
            commercial_use="Check Coinbase terms.", works_without_account=True,
        )

    def native_timeframes(self) -> list[str]:
        return list(self._TF)

    def instrument(self, provider_symbol: str) -> Instrument:
        sym = provider_symbol.upper()
        if "-" not in sym:
            raise ValidationError("Coinbase symbols look like BTC-USD")
        base, quote = sym.split("-", 1)
        return _crypto_instrument(self.name, sym, base, quote, "Coinbase")

    def search(self, query: str, limit: int = 20) -> list[Instrument]:
        data = self.get_json("/products")
        q = query.upper().replace("/", "-")
        out = [
            _crypto_instrument(self.name, d["id"], d["base_currency"], d["quote_currency"], "Coinbase",
                               float(d["quote_increment"]) if d.get("quote_increment") else None)
            for d in data if q in d.get("id", "") and d.get("status") == "online"
        ]
        out.sort(key=lambda i: (not i.provider_symbol.startswith(q), len(i.provider_symbol)))
        return out[:limit]

    def fetch_candles(self, instrument: Instrument, timeframe: str, *, start_ms: int | None = None,
                      end_ms: int | None = None, limit: int = 500) -> Candles:
        gran = self._TF.get(timeframe)
        if not gran:
            raise ValidationError(f"Coinbase does not serve {timeframe} natively")
        step_ms = gran * 1000
        end = end_ms or now_ms()
        start = start_ms if start_ms is not None else end - step_ms * limit
        rows: list[list[float]] = []
        cursor_end = end
        pages = 0
        while cursor_end > start and pages < 60:
            cursor_start = max(start, cursor_end - step_ms * 300)
            params = {
                "granularity": gran,
                "start": datetime.fromtimestamp(cursor_start / 1000, tz=timezone.utc).isoformat(),
                "end": datetime.fromtimestamp(cursor_end / 1000, tz=timezone.utc).isoformat(),
            }
            batch = self.get_json(f"/products/{instrument.provider_symbol}/candles", params)
            if isinstance(batch, dict):
                raise ProviderError(f"Coinbase: {batch.get('message', 'unexpected response')}")
            for r in batch:  # [time, low, high, open, close, volume]
                rows.append([int(r[0]) * 1000, float(r[3]), float(r[2]), float(r[1]), float(r[4]), float(r[5])])
            cursor_end = cursor_start
            pages += 1
        rows = [r for r in rows if start <= r[0] <= end]
        # dedupe across page borders
        uniq = {r[0]: r for r in rows}
        rows = list(uniq.values())
        last_closed = True
        if rows and end_ms is None:
            newest = max(r[0] for r in rows)
            last_closed = newest + step_ms <= now_ms()
        rows.sort(key=lambda r: r[0])
        return _finish(rows[-limit:] if start_ms is None else rows, instrument, timeframe, "Coinbase REST",
                       last_closed=last_closed)

    def fetch_quote(self, instrument: Instrument) -> Quote:
        d = self.get_json(f"/products/{instrument.provider_symbol}/ticker")
        if "price" not in d:
            raise ProviderError(f"Coinbase: no ticker for {instrument.provider_symbol}")
        ts = int(datetime.fromisoformat(d["time"].replace("Z", "+00:00")).timestamp() * 1000) if d.get("time") else now_ms()
        return Quote(instrument.id, float(d["price"]), ts, DataStatus.REALTIME, "Coinbase REST",
                     bid=float(d["bid"]) if d.get("bid") else None, ask=float(d["ask"]) if d.get("ask") else None,
                     volume_24h=float(d["volume"]) if d.get("volume") else None)


# ----------------------------------------------------------------------------------------------
class KrakenProvider(Provider):
    name = "KRAKEN"
    rate_per_s = 1.0
    burst = 3.0
    endpoints = ["https://api.kraken.com"]
    _TF = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440, "1w": 10080}

    def info(self) -> ProviderInfo:
        return ProviderInfo(
            name=self.name, display_name="Kraken (public)", markets=["crypto spot", "some fiat FX pairs"],
            live_status=DataStatus.REALTIME, requires_key=False, homepage="https://docs.kraken.com/api/",
            terms_note="Public REST endpoints; subject to Kraken terms.",
            timeframes=list(self._TF), max_history_note="OHLC endpoint returns only the most recent 720 bars per timeframe.",
            rate_limit_note="Venue: ~1 public call/s sustained. We self-limit to 1/s.",
            commercial_use="Check Kraken terms.", works_without_account=True,
        )

    def native_timeframes(self) -> list[str]:
        return list(self._TF)

    def instrument(self, provider_symbol: str) -> Instrument:
        sym = provider_symbol.upper()
        base, quote = (sym.split("/") + [""])[:2] if "/" in sym else (sym[:-3], sym[-3:])
        return _crypto_instrument(self.name, sym, base, quote, "Kraken")

    def search(self, query: str, limit: int = 20) -> list[Instrument]:
        data = self.get_json("/0/public/AssetPairs")
        q = query.upper().replace("/", "")
        out = []
        for key, d in (data.get("result") or {}).items():
            alt = d.get("altname", key)
            if q in alt and d.get("status", "online") == "online":
                ws = d.get("wsname", "")
                base, quote = ws.split("/") if "/" in ws else (alt[:-3], alt[-3:])
                out.append(_crypto_instrument(self.name, alt, base, quote, "Kraken"))
        out.sort(key=lambda i: (not i.provider_symbol.startswith(q), len(i.provider_symbol)))
        return out[:limit]

    def fetch_candles(self, instrument: Instrument, timeframe: str, *, start_ms: int | None = None,
                      end_ms: int | None = None, limit: int = 500) -> Candles:
        interval = self._TF.get(timeframe)
        if not interval:
            raise ValidationError(f"Kraken does not serve {timeframe} natively")
        params: dict[str, Any] = {"pair": instrument.provider_symbol.replace("/", ""), "interval": interval}
        if start_ms is not None:
            params["since"] = start_ms // 1000 - 1
        data = self.get_json("/0/public/OHLC", params)
        if data.get("error"):
            raise ProviderError(f"Kraken: {'; '.join(data['error'])}")
        result = data.get("result") or {}
        series = next((v for k, v in result.items() if k != "last"), [])
        rows = [[int(r[0]) * 1000, float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[6])] for r in series]
        if end_ms is not None:
            rows = [r for r in rows if r[0] <= end_ms]
        # Kraken's final row is the current, uncommitted bar
        last_closed = not (rows and end_ms is None and rows[-1][0] + interval * 60_000 > now_ms())
        rows = rows[-limit:] if start_ms is None else rows
        return _finish(rows, instrument, timeframe, "Kraken REST", last_closed=last_closed)


# ----------------------------------------------------------------------------------------------
class BinanceProvider(Provider):
    """Binance spot. ``api.binance.com`` is geo-blocked in some regions (HTTP 451); the
    official public market-data mirror ``data-api.binance.vision`` serves the same venue's data,
    so failing over between them never changes the instrument."""

    name = "BINANCE"
    rate_per_s = 8.0
    burst = 10.0
    endpoints = ["https://api.binance.com", "https://data-api.binance.vision"]
    _TF = ["1s", "1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "3d", "1w", "1M"]

    def info(self) -> ProviderInfo:
        return ProviderInfo(
            name=self.name, display_name="Binance spot (public market data)", markets=["crypto spot"],
            live_status=DataStatus.REALTIME, requires_key=False,
            homepage="https://developers.binance.com/docs/binance-spot-api-docs",
            terms_note="api.binance.com may be geo-restricted (HTTP 451); data-api.binance.vision is Binance's public mirror.",
            timeframes=self._TF, max_history_note="Full history, 1000 klines per request.",
            rate_limit_note="Venue: 6000 request-weight per minute per IP. We self-limit to 8/s.",
            commercial_use="Check Binance terms; regional restrictions apply.", works_without_account=True,
        )

    def native_timeframes(self) -> list[str]:
        return list(self._TF)

    def instrument(self, provider_symbol: str) -> Instrument:
        sym = provider_symbol.upper().replace("-", "").replace("/", "")
        for q in ("USDT", "USDC", "FDUSD", "BTC", "ETH", "BNB", "EUR", "TRY", "BRL", "JPY"):
            if sym.endswith(q) and len(sym) > len(q):
                return _crypto_instrument(self.name, sym, sym[: -len(q)], q, "Binance")
        raise ValidationError("Binance symbols look like BTCUSDT")

    def search(self, query: str, limit: int = 20) -> list[Instrument]:
        data = self.get_json("/api/v3/exchangeInfo", {"permissions": "SPOT", "symbolStatus": "TRADING"})
        q = query.upper().replace("-", "").replace("/", "")
        out = [
            _crypto_instrument(self.name, s["symbol"], s["baseAsset"], s["quoteAsset"], "Binance")
            for s in data.get("symbols", []) if q in s["symbol"]
        ]
        out.sort(key=lambda i: (not i.provider_symbol.startswith(q), len(i.provider_symbol)))
        return out[:limit]

    def fetch_candles(self, instrument: Instrument, timeframe: str, *, start_ms: int | None = None,
                      end_ms: int | None = None, limit: int = 500) -> Candles:
        if timeframe not in self._TF:
            raise ValidationError(f"Binance does not serve {timeframe} natively")
        rows: list[list[float]] = []
        if start_ms is None:
            params: dict[str, Any] = {"symbol": instrument.provider_symbol, "interval": timeframe, "limit": min(limit, 1000)}
            if end_ms:
                params["endTime"] = end_ms
            batch = self.get_json("/api/v3/klines", params)
            rows = [[int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]), int(r[6])] for r in batch]
            if limit > 1000:  # page backwards for more history
                while len(rows) < limit and rows:
                    params["endTime"] = rows[0][0] - 1
                    params["limit"] = min(1000, limit - len(rows))
                    batch = self.get_json("/api/v3/klines", params)
                    if not batch:
                        break
                    rows = [[int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]), int(r[6])]
                            for r in batch] + rows
        else:
            cursor = start_ms
            stop = end_ms or now_ms()
            pages = 0
            while cursor <= stop and pages < 100:
                batch = self.get_json("/api/v3/klines", {"symbol": instrument.provider_symbol, "interval": timeframe,
                                                         "startTime": cursor, "endTime": stop, "limit": 1000})
                if not batch:
                    break
                rows += [[int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]), int(r[6])] for r in batch]
                cursor = int(batch[-1][0]) + 1
                pages += 1
                if len(batch) < 1000:
                    break
        last_closed = not (rows and rows[-1][6] > now_ms())
        return _finish([r[:6] for r in rows], instrument, timeframe, "Binance REST", last_closed=last_closed)

    def fetch_quote(self, instrument: Instrument) -> Quote:
        d = self.get_json("/api/v3/ticker/24hr", {"symbol": instrument.provider_symbol})
        return Quote(instrument.id, float(d["lastPrice"]), int(d.get("closeTime") or time.time() * 1000),
                     DataStatus.REALTIME, "Binance REST", bid=float(d["bidPrice"]), ask=float(d["askPrice"]),
                     change_pct_24h=float(d["priceChangePercent"]), volume_24h=float(d["volume"]))


def check_timeframe(tf: str) -> str:
    parse(tf)
    return tf

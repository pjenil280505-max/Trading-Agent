"""MarketDataService: provider registry, local candle cache, aggregation and health.

Data flow for ``get_candles(instrument, timeframe)``:

1. Resolve the provider from the instrument id prefix (``OKX:...``). Never guess another venue.
2. Pick the base timeframe: the requested one if the venue serves it natively,
   otherwise the coarsest native timeframe that tiles it exactly (e.g. 45m <- 15m).
3. Read cached closed bars, fetch only what is new (incremental), store closed bars.
4. Validate + de-duplicate (``validation.clean``), aggregate if needed.
5. If the provider fails, serve the cache marked ``stale`` with status HISTORICAL —
   never as live. If there is no cache either, raise ``DataUnavailable``.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from omos.errors import DataUnavailable, NotFound, OmosError, ValidationError
from omos.marketdata.models import Candles, DataStatus, Instrument, Quote
from omos.marketdata.providers.base import Provider
from omos.marketdata.timeframes import aggregate, is_multiple, parse
from omos.marketdata.validation import clean
from omos.storage.db import Database
from omos.util import dumps, loads, now_ms

log = logging.getLogger(__name__)

DEFAULT_PROVIDER_SETTINGS: dict[str, dict[str, Any]] = {
    "OKX": {"enabled": True},
    "COINBASE": {"enabled": True},
    "KRAKEN": {"enabled": True},
    "BINANCE": {"enabled": True},
    "YAHOO": {"enabled": True},
    "FRED": {"enabled": True},
    "ECB": {"enabled": True},
    "CSV": {"enabled": True},
    "SIM": {"enabled": False},  # simulated prices: demo/test mode only
}


def build_providers(settings: dict[str, dict[str, Any]], store_reader=None) -> dict[str, Provider]:
    from omos.marketdata.providers.crypto import BinanceProvider, CoinbaseProvider, KrakenProvider, OKXProvider
    from omos.marketdata.providers.econ import ECBProvider, FredProvider
    from omos.marketdata.providers.local import CSVProvider, SimulatedProvider
    from omos.marketdata.providers.yahoo import YahooProvider

    classes: dict[str, Any] = {
        "OKX": OKXProvider, "COINBASE": CoinbaseProvider, "KRAKEN": KrakenProvider, "BINANCE": BinanceProvider,
        "YAHOO": YahooProvider, "FRED": FredProvider, "ECB": ECBProvider, "SIM": SimulatedProvider,
    }
    out: dict[str, Provider] = {}
    for name, cls in classes.items():
        if settings.get(name, {}).get("enabled", DEFAULT_PROVIDER_SETTINGS.get(name, {}).get("enabled", False)):
            out[name] = cls(settings=settings.get(name, {}))
    if settings.get("CSV", {}).get("enabled", True):
        out["CSV"] = CSVProvider(store_reader=store_reader)
    return out


@dataclass
class Health:
    provider: str
    status: str = "unknown"  # ok | degraded | down | unknown
    last_ok: int | None = None
    last_error: str | None = None
    last_error_at: int | None = None
    latency_ms: float | None = None
    requests: int = 0
    failures: int = 0
    consecutive_failures: int = 0
    rate_limited: int = 0
    recent: list[int] = field(default_factory=list)  # 1 ok / 0 fail for the last 20 calls

    def record(self, ok: bool, latency_ms: float | None = None, error: str | None = None) -> None:
        self.requests += 1
        self.recent = (self.recent + [1 if ok else 0])[-20:]
        if ok:
            self.last_ok = now_ms()
            self.latency_ms = latency_ms
            self.consecutive_failures = 0
        else:
            self.failures += 1
            self.consecutive_failures += 1
            self.last_error = (error or "")[:300]
            self.last_error_at = now_ms()
            if error and "rate limit" in error.lower():
                self.rate_limited += 1
        ratio = sum(self.recent) / len(self.recent)
        if self.consecutive_failures >= 3:
            self.status = "down"
        elif ratio < 0.8 or self.consecutive_failures:
            self.status = "degraded"
        else:
            self.status = "ok"

    def to_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d.pop("recent")
        d["success_rate_recent"] = round(sum(self.recent) / len(self.recent), 3) if self.recent else None
        return d


class CandleStore:
    def __init__(self, db: Database):
        self.db = db

    def upsert(self, c: Candles, *, include_open_bar: bool = False) -> int:
        if not len(c):
            return 0
        n = len(c) if (c.last_closed or include_open_bar) else len(c) - 1
        if n <= 0:
            return 0
        fetched = c.fetched_at or now_ms()
        rows = [
            (c.instrument_id, c.timeframe, int(c.ts[i]), float(c.open[i]), float(c.high[i]), float(c.low[i]),
             float(c.close[i]), float(c.volume[i]), c.source, c.status.value, 1, fetched)
            for i in range(n)
        ]
        self.db.executemany(
            "INSERT INTO candles(instrument_id, timeframe, ts, open, high, low, close, volume, source, status, is_closed, fetched_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(instrument_id, timeframe, ts) DO UPDATE SET "
            "open=excluded.open, high=excluded.high, low=excluded.low, close=excluded.close, volume=excluded.volume, "
            "source=excluded.source, status=excluded.status, is_closed=excluded.is_closed, fetched_at=excluded.fetched_at",
            rows,
        )
        return n

    def read(self, instrument_id: str, timeframe: str, start_ms: int | None = None, end_ms: int | None = None,
             limit: int | None = None) -> Candles:
        sql = "SELECT ts, open, high, low, close, volume, source, status, fetched_at FROM candles WHERE instrument_id=? AND timeframe=?"
        params: list[Any] = [instrument_id, timeframe]
        if start_ms is not None:
            sql += " AND ts >= ?"
            params.append(start_ms)
        if end_ms is not None:
            sql += " AND ts <= ?"
            params.append(end_ms)
        if limit is not None and start_ms is None:
            sql = f"SELECT * FROM ({sql} ORDER BY ts DESC LIMIT ?) ORDER BY ts ASC"
            params.append(int(limit))
        else:
            sql += " ORDER BY ts ASC"
        rows = self.db.conn.execute(sql, params).fetchall()
        if not rows:
            return Candles.from_rows([], instrument_id=instrument_id, timeframe=timeframe)
        statuses = Counter(r["status"] for r in rows)
        status = DataStatus(statuses.most_common(1)[0][0])
        return Candles.from_rows(
            [[r["ts"], r["open"], r["high"], r["low"], r["close"], r["volume"]] for r in rows],
            instrument_id=instrument_id, timeframe=timeframe, status=status, source=rows[-1]["source"],
            fetched_at=max(r["fetched_at"] for r in rows), last_closed=True,
        )

    def last_ts(self, instrument_id: str, timeframe: str) -> int | None:
        return self.db.scalar("SELECT MAX(ts) FROM candles WHERE instrument_id=? AND timeframe=?", (instrument_id, timeframe))

    def stats(self) -> list[dict[str, Any]]:
        return self.db.query(
            "SELECT instrument_id, timeframe, COUNT(*) AS bars, MIN(ts) AS first_ts, MAX(ts) AS last_ts, "
            "MAX(fetched_at) AS fetched_at FROM candles GROUP BY instrument_id, timeframe ORDER BY instrument_id"
        )


class MarketDataService:
    def __init__(self, db: Database, provider_settings: dict[str, dict[str, Any]] | None = None,
                 providers: dict[str, Provider] | None = None):
        self.db = db
        self.store = CandleStore(db)
        self.provider_settings = {**DEFAULT_PROVIDER_SETTINGS, **(provider_settings or db.get_setting("data.providers", {}) or {})}
        self.providers = providers if providers is not None else build_providers(self.provider_settings, self._csv_reader)
        self.health: dict[str, Health] = {name: Health(name) for name in self.providers}
        self._locks: dict[tuple[str, str], threading.Lock] = {}
        self._locks_guard = threading.Lock()
        self._instrument_cache: dict[str, Instrument] = {}

    # -- providers ------------------------------------------------------------------

    def reload_providers(self, provider_settings: dict[str, dict[str, Any]]) -> None:
        for p in self.providers.values():
            p.close()
        self.provider_settings = {**DEFAULT_PROVIDER_SETTINGS, **provider_settings}
        self.providers = build_providers(self.provider_settings, self._csv_reader)
        for name in self.providers:
            self.health.setdefault(name, Health(name))

    def register_provider(self, provider: Provider) -> None:
        """Add a plugin-supplied provider. Built-in names cannot be replaced."""
        if provider.name in DEFAULT_PROVIDER_SETTINGS:
            raise ValidationError(f"'{provider.name}' is a built-in provider name")
        self.providers[provider.name] = provider
        self.health.setdefault(provider.name, Health(provider.name))

    def unregister_provider(self, name: str) -> None:
        if name in DEFAULT_PROVIDER_SETTINGS:
            return
        p = self.providers.pop(name, None)
        if p:
            p.close()

    def provider(self, name: str) -> Provider:
        p = self.providers.get(name.upper())
        if not p:
            raise DataUnavailable(f"data provider '{name}' is not enabled (Settings > Data sources)")
        return p

    def provider_infos(self) -> list[dict[str, Any]]:
        from omos.marketdata.providers.crypto import BinanceProvider, CoinbaseProvider, KrakenProvider, OKXProvider
        from omos.marketdata.providers.econ import ECBProvider, FredProvider
        from omos.marketdata.providers.local import CSVProvider, SimulatedProvider
        from omos.marketdata.providers.yahoo import YahooProvider

        out = []
        for cls in (OKXProvider, CoinbaseProvider, KrakenProvider, BinanceProvider, YahooProvider, FredProvider,
                    ECBProvider, CSVProvider, SimulatedProvider):
            # info() only reads class attributes, so a disabled provider can describe itself unconstructed
            p = self.providers.get(cls.name) or cls.__new__(cls)
            d = p.info().to_dict()
            d["enabled"] = cls.name in self.providers
            d["health"] = self.health.get(cls.name, Health(cls.name)).to_dict()
            out.append(d)
        return out

    def _track(self, name: str, fn, *args, **kwargs):
        h = self.health.setdefault(name, Health(name))
        t0 = time.monotonic()
        try:
            result = fn(*args, **kwargs)
        except OmosError as exc:
            h.record(False, error=exc.message)
            raise
        except Exception as exc:  # unexpected parser errors etc. become DataUnavailable
            h.record(False, error=f"{type(exc).__name__}: {exc}")
            log.exception("provider %s failed", name)
            raise DataUnavailable(f"{name}: unexpected error ({type(exc).__name__})") from exc
        h.record(True, latency_ms=(time.monotonic() - t0) * 1000)
        return result

    # -- instruments ----------------------------------------------------------------

    def split_id(self, instrument_id: str) -> tuple[str, str]:
        if ":" not in instrument_id:
            raise ValidationError("instrument ids look like PROVIDER:SYMBOL, e.g. OKX:BTC-USDT or YAHOO:AAPL")
        prov, sym = instrument_id.split(":", 1)
        prov = prov.upper()
        if not sym or len(sym) > 64:
            raise ValidationError("invalid symbol")
        return prov, sym

    def instrument(self, instrument_id: str) -> Instrument:
        if instrument_id in self._instrument_cache:
            return self._instrument_cache[instrument_id]
        prov_name, sym = self.split_id(instrument_id)
        row = self.db.query_one("SELECT * FROM instruments WHERE id = ?", (f"{prov_name}:{sym}",))
        if row:
            inst = Instrument.from_dict({**row, "meta": loads(row["meta"], {})})
        else:
            prov = self.provider(prov_name)
            inst = self._track(prov_name, prov.instrument, sym)
            self.save_instrument(inst)
        self._instrument_cache[instrument_id] = inst
        return inst

    def save_instrument(self, inst: Instrument) -> None:
        d = inst.to_dict()
        self.db.execute(
            "INSERT INTO instruments(id, provider, provider_symbol, asset_class, exchange, base, quote, currency, name, "
            "timezone, session, tick_size, meta, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET name=excluded.name, exchange=excluded.exchange, session=excluded.session, "
            "timezone=excluded.timezone, meta=excluded.meta, updated_at=excluded.updated_at",
            (d["id"], d["provider"], d["provider_symbol"], d["asset_class"], d["exchange"], d["base"], d["quote"],
             d["currency"], d["name"], d["timezone"], d["session"], d["tick_size"], dumps(d["meta"]), now_ms()),
        )
        self._instrument_cache[inst.id] = inst

    def search(self, query: str, providers: list[str] | None = None, limit: int = 30) -> dict[str, Any]:
        query = query.strip()
        if not query or len(query) > 40:
            raise ValidationError("search text must be 1–40 characters")
        results: list[dict[str, Any]] = []
        errors: dict[str, str] = {}
        names = [p.upper() for p in providers] if providers else [n for n in self.providers if n != "CSV"]
        for name in names:
            p = self.providers.get(name)
            if not p:
                continue
            cache_key = f"search:{name}:{query.upper()}"
            cached = self.db.cache_get(cache_key)
            if cached is not None:
                results += cached
                continue
            try:
                found = [i.to_dict() for i in self._track(name, p.search, query, limit=10)]
                self.db.cache_set(cache_key, found, ttl_s=3600)
                results += found
            except OmosError as exc:
                errors[name] = exc.message
        return {"results": results[:limit], "errors": errors}

    # -- candles --------------------------------------------------------------------

    def _lock(self, key: tuple[str, str]) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(key, threading.Lock())

    def _base_timeframe(self, prov: Provider, timeframe: str) -> str:
        native = prov.native_timeframes()
        if timeframe in native:
            return timeframe
        target = parse(timeframe)
        candidates = [tf for tf in native if is_multiple(target, parse(tf))]
        if not candidates:
            raise ValidationError(f"{prov.name} cannot provide {timeframe} (native: {', '.join(native)})")
        return max(candidates, key=lambda t: parse(t).ms)

    def get_candles(self, instrument_id: str, timeframe: str, *, limit: int = 500, start_ms: int | None = None,
                    end_ms: int | None = None, refresh: bool = True, max_age_s: float | None = None) -> Candles:
        if limit <= 0 or limit > 50_000:
            raise ValidationError("limit must be between 1 and 50000")
        parse(timeframe)
        inst = self.instrument(instrument_id)
        prov = self.provider(inst.provider)
        base_tf = self._base_timeframe(prov, timeframe)
        ratio = max(1, parse(timeframe).ms // parse(base_tf).ms) if base_tf != timeframe else 1
        base_limit = min(50_000, limit * ratio + ratio)

        with self._lock((inst.id, base_tf)):
            cached = self.store.read(inst.id, base_tf, start_ms, end_ms, None if start_ms is not None else base_limit)
            fresh: Candles | None = None
            stale_reason = None
            if prov.name == "CSV":
                refresh = False
            need = refresh and self._needs_refresh(cached, base_tf, end_ms, max_age_s)
            if need:
                try:
                    fetch_start = start_ms
                    if len(cached) and start_ms is None and end_ms is None and len(cached) >= base_limit * 0.9:
                        fetch_start = int(cached.ts[-1])  # incremental: re-fetch from the newest cached bar
                    fresh = self._track(prov.name, prov.fetch_candles, inst, base_tf, start_ms=fetch_start,
                                        end_ms=end_ms, limit=base_limit)
                    self.store.upsert(fresh)
                except OmosError as exc:
                    stale_reason = exc.message
                    if not len(cached):
                        raise DataUnavailable(
                            f"{prov.name} could not supply {inst.id} {timeframe}: {exc.message}. No cached data either."
                        ) from exc
            merged = self._merge(cached, fresh, inst.id, base_tf)

        out = clean(merged, session_id=inst.session, now_ms=now_ms())
        if base_tf != timeframe:
            quality = out.quality
            out = aggregate(out, timeframe, now_ms=now_ms())
            out.quality = {**quality, "aggregated_from": base_tf}
        if start_ms is None and len(out) > limit:
            out = out.slice(len(out) - limit)
        if stale_reason:
            out.status = DataStatus.HISTORICAL
            out.quality = {**out.quality, "stale": True, "stale_reason": stale_reason,
                           "message": "Provider unavailable — showing cached bars only; not live."}
        elif fresh is None and len(out):
            out.quality = {**out.quality, "from_cache": True}
        return out

    def _needs_refresh(self, cached: Candles, base_tf: str, end_ms: int | None, max_age_s: float | None) -> bool:
        """Only closed bars are cached, so a request for the latest data always fetches
        (incrementally) unless the caller accepts cached data up to ``max_age_s`` old, or asked
        for a historical window the cache already covers."""
        if not len(cached):
            return True
        tf = parse(base_tf)
        if end_ms is not None and int(cached.ts[-1]) >= tf.floor(end_ms):
            return False
        if max_age_s is not None:
            return (now_ms() - cached.fetched_at) / 1000 > max_age_s
        return True

    @staticmethod
    def _merge(cached: Candles, fresh: Candles | None, instrument_id: str, tf: str) -> Candles:
        if fresh is None or not len(fresh):
            return cached
        if not len(cached):
            return fresh
        cut = int(fresh.ts[0])
        keep = cached.ts < cut
        rows = cached.slice(0, int(keep.sum())).rows() + fresh.rows()
        status = fresh.status
        return Candles.from_rows(rows, instrument_id=instrument_id, timeframe=tf, status=status, source=fresh.source,
                                 fetched_at=fresh.fetched_at, last_closed=fresh.last_closed)

    def _csv_reader(self, instrument_id: str, timeframe: str, start_ms, end_ms, limit) -> Candles:
        c = self.store.read(instrument_id, timeframe, start_ms, end_ms, limit)
        if not len(c):
            raise NotFound(f"no imported data for {instrument_id} at {timeframe}")
        return c

    def get_quote(self, instrument_id: str) -> Quote:
        inst = self.instrument(instrument_id)
        prov = self.provider(inst.provider)
        try:
            return self._track(prov.name, prov.fetch_quote, inst)
        except OmosError as exc:
            last = self.store.read(inst.id, prov.native_timeframes()[0], limit=1)
            if len(last):
                return Quote(inst.id, float(last.close[-1]), int(last.ts[-1]), DataStatus.HISTORICAL,
                             f"cache ({exc.message})")
            raise

    # -- imports ----------------------------------------------------------------------

    def import_csv(self, name: str, text: str, timeframe: str) -> dict[str, Any]:
        from omos.marketdata.providers.local import CSVProvider, parse_csv

        safe = "".join(ch for ch in name.upper() if ch.isalnum() or ch in "-_.")[:40]
        if not safe:
            raise ValidationError("name must contain letters or digits")
        parse(timeframe)
        rows = parse_csv(text)
        inst = CSVProvider().instrument(safe)
        self.save_instrument(inst)
        c = Candles.from_rows(rows, instrument_id=inst.id, timeframe=timeframe, status=DataStatus.HISTORICAL,
                              source=f"CSV import '{name}'", fetched_at=now_ms(), last_closed=True)
        c = clean(c)
        n = self.store.upsert(c)
        return {"instrument": inst.to_dict(), "imported": n, "quality": c.quality}

    def health_report(self) -> list[dict[str, Any]]:
        return [h.to_dict() for h in self.health.values()]

    def persist_health(self) -> None:
        for h in self.health.values():
            self.db.execute(
                "INSERT INTO provider_health(provider, status, last_ok, last_error, last_error_at, latency_ms, requests, "
                "failures, rate_limited, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(provider) DO UPDATE SET "
                "status=excluded.status, last_ok=excluded.last_ok, last_error=excluded.last_error, "
                "last_error_at=excluded.last_error_at, latency_ms=excluded.latency_ms, requests=excluded.requests, "
                "failures=excluded.failures, rate_limited=excluded.rate_limited, updated_at=excluded.updated_at",
                (h.provider, h.status, h.last_ok, h.last_error, h.last_error_at, h.latency_ms, h.requests, h.failures,
                 h.rate_limited, now_ms()),
            )


def candles_to_payload(c: Candles) -> dict[str, Any]:
    return {**c.meta_dict(), "bars": c.rows()}


def closes(c: Candles) -> np.ndarray:
    return c.close

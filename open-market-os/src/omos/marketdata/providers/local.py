"""Local providers.

* ``SIM`` — deterministic simulated prices, ALWAYS labelled SIMULATED. Disabled
  unless the user turns on demo mode; used by tests and offline demos. These are
  not real prices and the UI shows a SIMULATED banner on every chart that uses them.
* ``CSV`` — candles the user imported from a file; labelled HISTORICAL with the
  file name as the source.
"""

from __future__ import annotations

import csv
import hashlib
import io
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from omos.errors import NotFound, ProviderError, ValidationError
from omos.marketdata.models import AssetClass, Candles, DataStatus, Instrument
from omos.marketdata.providers.base import Provider, ProviderInfo
from omos.marketdata.timeframes import parse
from omos.util import now_ms


class SimulatedProvider(Provider):
    name = "SIM"
    rate_per_s = 1000.0
    burst = 1000.0
    SYMBOLS = {"DEMO-TREND": (100.0, 0.25, 0.6), "DEMO-RANGE": (50.0, 0.0, 0.35), "DEMO-VOLATILE": (20.0, 0.1, 1.2)}

    def info(self) -> ProviderInfo:
        return ProviderInfo(
            name=self.name, display_name="Simulated data (demo/test only)", markets=["simulated"],
            live_status=DataStatus.SIMULATED, requires_key=False, homepage="",
            terms_note="Generated locally. NOT real market prices. Only available in demo mode.",
            timeframes=["1m", "5m", "15m", "1h", "4h", "1d"], max_history_note="Unlimited (deterministic generator).",
            rate_limit_note="None (local).", commercial_use="n/a", works_without_account=True,
        )

    def native_timeframes(self) -> list[str]:
        return ["1m", "5m", "15m", "1h", "4h", "1d"]

    def instrument(self, provider_symbol: str) -> Instrument:
        sym = provider_symbol.upper()
        if sym not in self.SYMBOLS:
            raise NotFound(f"simulated symbols: {', '.join(self.SYMBOLS)}")
        return Instrument(id=f"SIM:{sym}", provider="SIM", provider_symbol=sym, asset_class=AssetClass.CRYPTO,
                          name=f"{sym} (SIMULATED)", exchange="Simulator", quote="USD", currency="USD",
                          session="24x7", meta={"simulated": True})

    def search(self, query: str, limit: int = 20) -> list[Instrument]:
        q = query.upper()
        return [self.instrument(s) for s in self.SYMBOLS if q in s][:limit]

    def fetch_candles(self, instrument: Instrument, timeframe: str, *, start_ms: int | None = None,
                      end_ms: int | None = None, limit: int = 500) -> Candles:
        tf = parse(timeframe)
        step = tf.ms
        end = tf.floor(end_ms if end_ms is not None else now_ms())
        start = tf.floor(start_ms) if start_ms is not None else end - step * (limit - 1)
        ts = np.arange(start, end + 1, step, dtype=np.int64)
        if start_ms is None:
            ts = ts[-limit:]
        p0, drift, vol = self.SYMBOLS[instrument.provider_symbol]
        rows = self._generate(instrument.provider_symbol, ts, step, p0, drift, vol)
        return Candles.from_rows(rows, instrument_id=instrument.id, timeframe=timeframe, status=DataStatus.SIMULATED,
                                 source="Simulator (not real prices)", fetched_at=now_ms(),
                                 last_closed=(end_ms is not None) or (end + step <= now_ms()))

    @staticmethod
    def _generate(sym: str, ts: np.ndarray, step: int, p0: float, drift: float, vol: float) -> list[list[float]]:
        """Price is a pure function of (symbol, timestamp) so every call agrees with every other call."""
        if len(ts) == 0:
            return []
        seed = int(hashlib.sha256(sym.encode()).hexdigest()[:8], 16)
        years = (ts.astype(np.float64) - 1.6e12) / (365.25 * 86_400_000)
        # smooth deterministic "random walk": sum of incommensurate sinusoids + drift
        phase = seed % 1000
        base = (drift * years
                + 0.18 * vol * np.sin(years * 9.1 + phase)
                + 0.07 * vol * np.sin(years * 57.3 + phase * 1.7)
                + 0.025 * vol * np.sin(years * 403.0 + phase * 2.3)
                + 0.01 * vol * np.sin(years * 3011.0 + phase * 3.1))
        close = p0 * np.exp(base)
        open_ = np.r_[close[0], close[:-1]]
        wiggle = 0.004 * vol * np.abs(np.sin(years * 77_777.0 + phase)) * math.sqrt(step / 60_000) ** 0.5
        high = np.maximum(open_, close) * (1 + wiggle)
        low = np.minimum(open_, close) * (1 - wiggle)
        volume = 1000 * (1 + np.abs(np.sin(years * 1234.5 + phase)))
        return [[int(t), float(o), float(h), float(lo), float(c), float(v)]
                for t, o, h, lo, c, v in zip(ts, open_, high, low, close, volume)]


class CSVProvider(Provider):
    """Serves data the user imported. Reads from the candle store; it never fetches."""

    name = "CSV"

    def __init__(self, store_reader=None, **kw):
        super().__init__(**kw)
        self.store_reader = store_reader  # callable(instrument_id, timeframe, start, end, limit) -> Candles

    def info(self) -> ProviderInfo:
        return ProviderInfo(
            name=self.name, display_name="Imported CSV files", markets=["any (user-supplied)"],
            live_status=DataStatus.HISTORICAL, requires_key=False, homepage="",
            terms_note="Data you imported. You are responsible for its licence.", timeframes=["any"],
            max_history_note="Whatever the file contains.", rate_limit_note="None (local).", commercial_use="n/a",
            works_without_account=True,
        )

    def native_timeframes(self) -> list[str]:
        return ["1m", "5m", "15m", "1h", "4h", "1d", "1w"]

    def instrument(self, provider_symbol: str) -> Instrument:
        return Instrument(id=f"CSV:{provider_symbol}", provider="CSV", provider_symbol=provider_symbol,
                          asset_class=AssetClass.EQUITY, name=f"{provider_symbol} (imported)", exchange="CSV import")

    def search(self, query: str, limit: int = 20) -> list[Instrument]:
        return []

    def fetch_candles(self, instrument: Instrument, timeframe: str, *, start_ms: int | None = None,
                      end_ms: int | None = None, limit: int = 500) -> Candles:
        if self.store_reader is None:
            raise ProviderError("CSV provider has no store")
        return self.store_reader(instrument.id, timeframe, start_ms, end_ms, limit)


def parse_csv(text: str, *, max_rows: int = 2_000_000) -> list[list[float]]:
    """Parse OHLCV CSV. Accepts a header with time/date, open, high, low, close[, volume].

    Time may be epoch seconds, epoch milliseconds, ISO date or ISO datetime (UTC assumed).
    """
    reader = csv.reader(io.StringIO(text))
    header = [h.strip().lower() for h in next(reader, [])]
    if not header:
        raise ValidationError("empty CSV")

    def col(*names: str) -> int:
        for n in names:
            if n in header:
                return header.index(n)
        raise ValidationError(f"CSV needs a column named one of: {', '.join(names)}")

    it = col("time", "timestamp", "date", "datetime", "ts")
    io_, ih, il, ic = col("open", "o"), col("high", "h"), col("low", "l"), col("close", "c", "adj close")
    iv = header.index("volume") if "volume" in header else (header.index("v") if "v" in header else None)
    rows = []
    for k, rec in enumerate(reader):
        if k >= max_rows:
            raise ValidationError(f"CSV has more than {max_rows} rows")
        if not rec or len(rec) < len(header):
            continue
        try:
            rows.append([_parse_time(rec[it]), float(rec[io_]), float(rec[ih]), float(rec[il]), float(rec[ic]),
                         float(rec[iv]) if iv is not None and rec[iv] not in ("", None) else 0.0])
        except ValueError as exc:
            raise ValidationError(f"CSV row {k + 2}: {exc}") from exc
    if not rows:
        raise ValidationError("CSV contains no data rows")
    return rows


def _parse_time(s: str) -> int:
    s = s.strip()
    if s.replace(".", "", 1).isdigit():
        v = float(s)
        return int(v if v > 1e11 else v * 1000)
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return int(datetime.strptime(s.replace("Z", ""), fmt).replace(tzinfo=timezone.utc).timestamp() * 1000)
        except ValueError:
            continue
    try:
        return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError as exc:
        raise ValueError(f"unrecognised time '{s}'") from exc


def safe_import_path(path: str, allowed_root: Path) -> Path:
    """Resolve a user-supplied import path and refuse anything outside ``allowed_root``."""
    p = Path(path).expanduser().resolve()
    root = allowed_root.resolve()
    if root != p and root not in p.parents:
        raise ValidationError(f"imports must come from {root}")
    return p

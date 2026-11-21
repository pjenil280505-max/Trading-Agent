import json
from datetime import datetime, timezone

import httpx
import numpy as np
import pytest

from omos.errors import DataUnavailable, ValidationError
from omos.marketdata.models import AssetClass, Candles, DataStatus, Instrument
from omos.marketdata.providers.base import Provider, ProviderInfo
from omos.marketdata.providers.crypto import BinanceProvider, CoinbaseProvider, OKXProvider
from omos.marketdata.providers.local import SimulatedProvider, parse_csv, safe_import_path
from omos.marketdata.ratelimit import RetryableError, TokenBucket, retry
from omos.marketdata.service import MarketDataService
from omos.marketdata.sessions import get_session
from omos.marketdata.timeframes import aggregate, heikin_ashi, is_multiple, parse
from omos.marketdata.validation import clean, find_gaps


def ms(y, m, d, hh=0, mm=0):
    return int(datetime(y, m, d, hh, mm, tzinfo=timezone.utc).timestamp() * 1000)


# ---------------------------------------------------------------- timeframes
def test_parse_timeframes():
    assert parse("15m").ms == 900_000
    assert parse("1M").is_monthly and not parse("1m").is_monthly
    with pytest.raises(ValidationError):
        parse("15x")
    with pytest.raises(ValidationError):
        parse("0m")


def test_floor_alignment():
    t = ms(2026, 3, 18, 13, 47)
    assert parse("15m").floor(t) == ms(2026, 3, 18, 13, 45)
    assert parse("4h").floor(t) == ms(2026, 3, 18, 12, 0)
    assert parse("1d").floor(t) == ms(2026, 3, 18)
    assert parse("1w").floor(t) == ms(2026, 3, 16)  # Monday
    assert parse("1M").floor(t) == ms(2026, 3, 1)
    assert parse("1M").next_open(ms(2026, 12, 1)) == ms(2027, 1, 1)


def test_is_multiple():
    assert is_multiple(parse("45m"), parse("15m"))
    assert not is_multiple(parse("45m"), parse("30m"))
    assert is_multiple(parse("1M"), parse("1d"))
    assert not is_multiple(parse("1m"), parse("5m"))


def test_aggregate_exact(candles_factory):
    c = candles_factory([1, 2, 3, 4, 5, 6], start=ms(2026, 1, 1), step=60_000)
    a = aggregate(c, "3m")
    assert len(a) == 2
    assert a.open[0] == c.open[0] and a.close[0] == 3 and a.close[1] == 6
    assert a.high[0] == c.high[:3].max() and a.low[1] == c.low[3:].min()
    assert a.volume[0] == c.volume[:3].sum()


def test_aggregate_partial_bucket_flagged(candles_factory):
    c = candles_factory([1, 2, 3, 4], start=ms(2026, 1, 1), step=60_000)
    a = aggregate(c, "3m", now_ms=ms(2026, 1, 1, 0, 4))
    assert len(a) == 2 and a.last_closed is False


def test_heikin_ashi(candles_factory):
    c = candles_factory([10, 11, 12, 11, 13])
    ha = heikin_ashi(c)
    assert np.allclose(ha.close, (c.open + c.high + c.low + c.close) / 4)
    assert (ha.high >= np.maximum(ha.open, ha.close) - 1e-9).all()


# ---------------------------------------------------------------- validation
def test_clean_removes_duplicates_invalid_and_reports(candles_factory):
    c = candles_factory([10, 11, 12, 13])
    c.ts = np.r_[c.ts[:3], c.ts[2]]  # duplicate timestamp
    c.high[1] = c.low[1] - 1        # high < low -> invalid
    out = clean(c)
    q = out.quality
    assert q["duplicates_removed"] == 1
    assert q["invalid_dropped"] == 1
    assert len(out) == 2
    assert q["score"] < 100


def test_gap_detection_24x7(candles_factory):
    c = candles_factory([1, 2, 3, 4, 5])
    c.ts = np.array([0, 60_000, 120_000, 600_000, 660_000]) + ms(2026, 1, 1)
    gaps = find_gaps(c)
    assert gaps == [{"after": int(c.ts[2]), "before": int(c.ts[3]), "missing_bars": 7}]


def test_gap_detection_ignores_closed_session(candles_factory):
    # NYSE 15:59 -> next day 09:30 New York (14:30 UTC) is not a gap
    t1 = ms(2026, 3, 17, 19, 59)  # 15:59 EDT
    t2 = ms(2026, 3, 18, 13, 30)  # 09:30 EDT
    c = candles_factory([1, 2])
    c.ts = np.array([t1, t2])
    assert find_gaps(c, session_id="xnys") == []


def test_sessions():
    nyse = get_session("xnys")
    assert nyse.is_open(ms(2026, 3, 18, 15, 0))       # Wednesday 11:00 EDT
    assert not nyse.is_open(ms(2026, 3, 21, 15, 0))   # Saturday
    fx = get_session("fx24x5")
    assert fx.is_open(ms(2026, 3, 18, 12, 0))
    assert not fx.is_open(ms(2026, 3, 21, 12, 0))     # Saturday
    assert get_session("24x7").is_open(ms(2026, 3, 21, 12, 0))


# ---------------------------------------------------------------- rate limiting / retry
def test_token_bucket():
    b = TokenBucket(rate=1000, capacity=2)
    assert b.try_acquire() and b.try_acquire()
    assert not b.try_acquire()


def test_retry_backoff():
    calls = {"n": 0}
    delays = []

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RetryableError("transient")
        return "ok"

    assert retry(flaky, attempts=3, base_delay=0.1, sleep=delays.append) == "ok"
    assert len(delays) == 2 and delays[1] > delays[0]


# ---------------------------------------------------------------- providers with mocked HTTP
def mock_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_okx_parsing_and_open_bar_flag():
    def handler(req):
        assert req.url.path == "/api/v5/market/candles"
        return httpx.Response(200, json={"code": "0", "data": [
            ["1700000120000", "3", "4", "2", "3.5", "10", "0", "0", "0"],   # newest, not confirmed
            ["1700000060000", "2", "3", "1", "2.5", "11", "0", "0", "1"],
        ]})
    p = OKXProvider(client=mock_client(handler))
    c = p.fetch_candles(p.instrument("BTC-USDT"), "1m", limit=2)
    assert list(c.ts) == [1700000060000, 1700000120000]
    assert c.close[-1] == 3.5 and c.last_closed is False
    assert c.status == DataStatus.REALTIME


def test_coinbase_column_order():
    def handler(req):
        # Coinbase: [time, low, high, open, close, volume], newest first
        return httpx.Response(200, json=[[1700000060, 1.0, 5.0, 2.0, 4.0, 9.0]])
    p = CoinbaseProvider(client=mock_client(handler))
    c = p.fetch_candles(p.instrument("BTC-USD"), "1m", start_ms=1700000000000, end_ms=1700000100000)
    assert (c.open[0], c.high[0], c.low[0], c.close[0], c.volume[0]) == (2.0, 5.0, 1.0, 4.0, 9.0)


def test_binance_endpoint_failover_on_geoblock():
    seen = []

    def handler(req):
        seen.append(req.url.host)
        if req.url.host == "api.binance.com":
            return httpx.Response(451, json={"msg": "restricted location"})
        return httpx.Response(200, json=[[1700000000000, "1", "2", "0.5", "1.5", "7", 1700000059999, "0", 1, "0", "0", "0"]])
    p = BinanceProvider(client=mock_client(handler))
    c = p.fetch_candles(p.instrument("BTCUSDT"), "1m", limit=1)
    assert seen == ["api.binance.com", "data-api.binance.vision"]
    assert len(c) == 1 and c.close[0] == 1.5
    # the working endpoint is remembered
    p.fetch_candles(p.instrument("BTCUSDT"), "1m", limit=1)
    assert seen[-1] == "data-api.binance.vision"


def test_provider_retries_then_reports_unavailable():
    from omos.errors import ProviderError

    def handler(req):
        return httpx.Response(503)
    p = OKXProvider(client=mock_client(handler))
    import omos.marketdata.ratelimit as rl
    orig = rl.time.sleep
    rl.time.sleep = lambda s: None
    try:
        with pytest.raises(ProviderError):
            p.fetch_candles(p.instrument("BTC-USDT"), "1m", limit=1)
    finally:
        rl.time.sleep = orig


def test_simulated_is_deterministic_and_labelled():
    p = SimulatedProvider()
    inst = p.instrument("DEMO-TREND")
    a = p.fetch_candles(inst, "1h", end_ms=ms(2026, 1, 10), limit=50)
    b = p.fetch_candles(inst, "1h", end_ms=ms(2026, 1, 10), limit=50)
    assert np.array_equal(a.close, b.close)
    assert a.status == DataStatus.SIMULATED
    assert "not real" in a.source.lower()


def test_csv_parsing_and_path_guard(tmp_path):
    rows = parse_csv("date,open,high,low,close,volume\n2026-01-02,1,2,0.5,1.5,100\n2026-01-03,1.5,2.5,1,2,200\n")
    assert rows[0][0] == ms(2026, 1, 2) and rows[1][4] == 2.0
    with pytest.raises(ValidationError):
        parse_csv("foo,bar\n1,2\n")
    root = tmp_path / "imports"
    root.mkdir()
    assert safe_import_path(str(root / "a.csv"), root) == (root / "a.csv").resolve()
    with pytest.raises(ValidationError):
        safe_import_path(str(tmp_path / ".." / "etc" / "passwd"), root)


# ---------------------------------------------------------------- service
class FakeProvider(Provider):
    name = "FAKE"
    rate_per_s = 1000
    burst = 1000

    def __init__(self, series=None, fail=False):
        super().__init__(client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))))
        self.series = series
        self.fail = fail
        self.calls = []

    def info(self):
        return ProviderInfo("FAKE", "Fake", ["test"], DataStatus.REALTIME, False, "", "", ["1m"], "", "", "", True)

    def native_timeframes(self):
        return ["1m", "15m"]

    def instrument(self, sym):
        return Instrument(f"FAKE:{sym}", "FAKE", sym, AssetClass.CRYPTO, name=sym)

    def search(self, q, limit=20):
        return [self.instrument("ABC")]

    def fetch_candles(self, inst, tf, *, start_ms=None, end_ms=None, limit=500):
        from omos.errors import ProviderError
        self.calls.append((tf, start_ms, end_ms, limit))
        if self.fail:
            raise ProviderError("FAKE down")
        rows = self.series(tf, start_ms, end_ms, limit)
        return Candles.from_rows(rows, instrument_id=inst.id, timeframe=tf, status=DataStatus.REALTIME, source="FAKE",
                                 fetched_at=1, last_closed=True)


def series_fn(base=ms(2026, 1, 1), n=120):
    def f(tf, start, end, limit):
        step = 60_000 if tf == "1m" else 900_000
        all_rows = [[base + i * step, 10 + i, 11 + i, 9 + i, 10.5 + i, 1.0] for i in range(n)]
        if start is not None:
            all_rows = [r for r in all_rows if r[0] >= start]
        return all_rows[-limit:]
    return f


def test_service_caches_aggregates_and_labels(db):
    fake = FakeProvider(series_fn())
    svc = MarketDataService(db, providers={"FAKE": fake})
    c = svc.get_candles("FAKE:ABC", "1m", limit=50)
    assert len(c) == 50 and c.status == DataStatus.REALTIME
    assert db.scalar("SELECT COUNT(*) FROM candles") >= 50
    agg = svc.get_candles("FAKE:ABC", "45m", limit=2)  # served from 15m base
    assert agg.quality["aggregated_from"] == "15m"
    assert "aggregated" in agg.source


def test_service_serves_stale_cache_honestly_when_provider_fails(db):
    fake = FakeProvider(series_fn())
    svc = MarketDataService(db, providers={"FAKE": fake})
    svc.get_candles("FAKE:ABC", "1m", limit=30)
    fake.fail = True
    c = svc.get_candles("FAKE:ABC", "1m", limit=30)
    assert c.status == DataStatus.HISTORICAL       # never presented as live
    assert c.quality["stale"] is True
    assert svc.health["FAKE"].status in ("degraded", "down")


def test_service_raises_when_no_data_at_all(db):
    svc = MarketDataService(db, providers={"FAKE": FakeProvider(series_fn(), fail=True)})
    with pytest.raises(DataUnavailable):
        svc.get_candles("FAKE:ABC", "1m", limit=10)


def test_service_never_substitutes_instruments(db):
    svc = MarketDataService(db, providers={"FAKE": FakeProvider(series_fn())})
    with pytest.raises(DataUnavailable):
        svc.get_candles("OKX:BTC-USDT", "1m")  # OKX not enabled -> refuse, don't fall back to FAKE
    with pytest.raises(ValidationError):
        svc.get_candles("NOPREFIX", "1m")


def test_service_incremental_fetch(db):
    fake = FakeProvider(series_fn(n=200))
    svc = MarketDataService(db, providers={"FAKE": fake})
    svc.get_candles("FAKE:ABC", "1m", limit=100)
    svc.get_candles("FAKE:ABC", "1m", limit=100)
    # the second call starts from the newest cached bar instead of refetching everything
    assert fake.calls[-1][1] is not None


def test_csv_import_roundtrip(db):
    svc = MarketDataService(db, providers={})
    from omos.marketdata.providers.local import CSVProvider
    svc.providers["CSV"] = CSVProvider(store_reader=svc._csv_reader)
    text = "time,open,high,low,close,volume\n" + "\n".join(
        f"{1700000000 + i * 86400},{10 + i},{11 + i},{9 + i},{10.5 + i},5" for i in range(30))
    res = svc.import_csv("my data", text, "1d")
    assert res["imported"] == 30
    c = svc.get_candles(res["instrument"]["id"], "1d", limit=10)
    assert len(c) == 10 and c.status == DataStatus.HISTORICAL


def test_search_aggregates_and_caches(db):
    fake = FakeProvider(series_fn())
    svc = MarketDataService(db, providers={"FAKE": fake})
    r = svc.search("abc")
    assert r["results"][0]["id"] == "FAKE:ABC"
    with pytest.raises(ValidationError):
        svc.search("")


def test_provider_infos_cover_all_providers(db):
    svc = MarketDataService(db)
    infos = {i["name"]: i for i in svc.provider_infos()}
    assert {"OKX", "COINBASE", "KRAKEN", "BINANCE", "YAHOO", "FRED", "ECB", "CSV", "SIM"} <= set(infos)
    assert infos["SIM"]["enabled"] is False  # simulated data off by default
    assert infos["YAHOO"]["live_status"] == "DELAYED"
    json.dumps(infos)

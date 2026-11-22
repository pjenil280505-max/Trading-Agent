"""Live streaming: venue message parsing, reconnects, LIVE/STALE labels, chart updates and paper fills from ticks."""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time

import numpy as np
import pytest

from helpers import ControlledProvider
from omos.marketdata.models import DataStatus
from omos.marketdata.streaming import VENUES, StreamManager, Tick


def test_venue_parsers_turn_messages_into_ticks():
    cb = VENUES["COINBASE"].parse(json.dumps({"type": "ticker", "product_id": "BTC-USD", "price": "60546.76",
                                              "time": "2026-11-22T14:00:00.123Z", "last_size": "0.01",
                                              "best_bid": "60546.5", "best_ask": "60547.0"}), {"BTC-USD": "COINBASE:BTC-USD"})
    assert cb[0].instrument_id == "COINBASE:BTC-USD" and cb[0].price == 60546.76 and cb[0].bid == 60546.5
    assert cb[0].ts == 1795356000123
    kr = VENUES["KRAKEN"].parse(json.dumps({"channel": "ticker", "type": "update", "data": [
        {"symbol": "BTC/USD", "bid": 60554.3, "ask": 60554.4, "last": 60554.4}]}), {"BTC/USD": "KRAKEN:XBTUSD"})
    assert kr[0].instrument_id == "KRAKEN:XBTUSD" and kr[0].ask == 60554.4
    bn = VENUES["BINANCE"].parse(json.dumps({"stream": "btcusdt@trade", "data": {
        "e": "trade", "s": "BTCUSDT", "p": "60640.0", "q": "0.008", "T": 1795357924332}}), {"btcusdt": "BINANCE:BTCUSDT"})
    assert bn[0].price == 60640.0 and bn[0].size == 0.008 and bn[0].ts == 1795357924332
    ok = VENUES["OKX"].parse(json.dumps({"arg": {"channel": "trades", "instId": "BTC-USDT"}, "data": [
        {"instId": "BTC-USDT", "px": "60600.1", "sz": "0.5", "ts": "1795357924000"}]}), {"BTC-USDT": "OKX:BTC-USDT"})
    assert ok[0].price == 60600.1 and ok[0].size == 0.5
    assert VENUES["OKX"].parse("pong", {}) == []
    # anything else (heartbeats, subscriptions acks, unknown symbols, broken prices) is ignored
    assert VENUES["COINBASE"].parse(json.dumps({"type": "subscriptions"}), {}) == []
    assert VENUES["COINBASE"].parse(json.dumps({"type": "ticker", "product_id": "ETH-USD", "price": "1"}), {"BTC-USD": "x"}) == []
    assert VENUES["BINANCE"].parse(json.dumps({"data": {"e": "trade", "s": "BTCUSDT", "p": "nan"}}), {"btcusdt": "x"}) == []


def test_kraken_symbols_map_to_its_stream_names():
    k = VENUES["KRAKEN"]
    assert [k.stream_symbol(s) for s in ("XBTUSD", "ETHUSD", "XDGUSD", "SOLUSDT", "ETHXBT")] == \
        ["BTC/USD", "ETH/USD", "DOGE/USD", "SOL/USDT", "ETH/BTC"]
    assert VENUES["BINANCE"].url(["btcusdt", "ethusdt"], 1).endswith("/stream?streams=btcusdt@trade/ethusdt@trade")
    assert "binance.vision" in VENUES["BINANCE"].url(["btcusdt"], 1)  # second attempt: the public mirror


class FakeSocket:
    def __init__(self, url, script):
        self.url, self.sent, self.closed = url, [], False
        self.q: asyncio.Queue = asyncio.Queue()
        for m in script:
            self.q.put_nowait(m)

    async def send(self, m):
        self.sent.append(m)

    async def recv(self):
        m = await self.q.get()
        if isinstance(m, Exception):
            raise m
        return m

    async def close(self):
        self.closed = True


def test_stream_manager_subscribes_delivers_and_reconnects():
    ticks, sockets = [], []
    msg = json.dumps({"type": "ticker", "product_id": "BTC-USD", "price": "100.5", "time": "2026-11-22T14:00:00Z"})

    async def connect(url):
        if len(sockets) == 0:
            sockets.append(None)
            raise ConnectionResetError("first attempt fails")
        s = FakeSocket(url, [msg, msg])
        sockets.append(s)
        return s

    m = StreamManager(ticks.append, connect=connect, max_backoff_s=0.05)
    m.start()
    try:
        m.want("COINBASE:BTC-USD")
        m.want("SIM:NOT-STREAMED")  # venues without a stream are left to polling
        end = time.time() + 10
        while len(ticks) < 2 and time.time() < end:
            time.sleep(0.05)
        assert [t.price for t in ticks[:2]] == [100.5, 100.5]
        live = [s for s in sockets if s]
        assert json.loads(live[0].sent[0]) == {"type": "subscribe", "product_ids": ["BTC-USD"], "channels": ["ticker"]}
        st = m.status()["COINBASE"]
        assert st["connected"] and st["errors"] >= 1 and st["symbols"] == ["BTC-USD"] and "SIM" not in m.status()
        m.want("COINBASE:ETH-USD")  # a new symbol: reconnect with both
        end = time.time() + 10
        while time.time() < end and not any(s and "ETH-USD" in "".join(s.sent) for s in sockets):
            time.sleep(0.05)
        assert any(s and json.loads(s.sent[0])["product_ids"] == ["BTC-USD", "ETH-USD"] for s in sockets)
    finally:
        m.stop()


# ----------------------------------------------------------------------------------- host integration
@pytest.fixture
def host(tmp_path, monkeypatch):
    from omos.config import HostConfig
    from omos.host import Host
    monkeypatch.setenv("OMOS_DISABLE_KEYRING", "1")
    monkeypatch.setenv("OMOS_VAULT_PASSPHRASE", "test vault passphrase")
    prov = ControlledProvider(step_ms=60_000, start_ms=(int(time.time() * 1000) // 60_000 - 200) * 60_000)
    prov.set_closes("ABC", np.linspace(100, 101, 200), open_last=True)
    h = Host(HostConfig(data_dir=str(tmp_path / "home")), start_loops=False, providers={"TEST": prov})
    yield h, prov
    h.db.close()


def test_ticks_make_quotes_and_charts_live_and_fill_paper_orders(host):
    from omos.execution.models import OrderRequest, OrderType, Side
    h, prov = host
    events = []
    h.bus.subscribe(lambda e: events.append((e["topic"], e["data"])) if e["topic"] == "candle" else None)
    h.subscribe("TEST:ABC", "1m")
    h._poll_market()  # first polled bar: the venue's open, high, low
    first = [d for t, d in events][-1]
    assert first["status"] == "REALTIME" and not first["closed"]
    bar_ts = first["bar"][0]
    acct = h.db.query_one("SELECT id FROM accounts")["id"]
    h.gateway.on_price("TEST:ABC", 101.0, "REALTIME")
    resting = h.gateway.submit(OrderRequest(account_id=acct, instrument_id="TEST:ABC", side=Side.BUY, qty=1,
                                            type=OrderType.LIMIT, limit_price=100.2, stop_loss=95), actor="ui")["order"]
    assert resting["status"] == "accepted"
    h._on_tick(Tick("TEST:ABC", 100.1, bar_ts + 5_000, 0.3, 100.05, 100.15))
    live = [d for t, d in events][-1]
    assert live["status"] == "LIVE" and live["bar"][4] == 100.1 and live["bar"][3] <= 100.1 and live["bar"][0] == bar_ts
    q = h.market.get_quote("TEST:ABC")
    assert q.status == DataStatus.LIVE and q.price == 100.1 and q.bid == 100.05
    assert h.db.query_one("SELECT status FROM orders WHERE id = ?", (resting["id"],))["status"] == "filled"
    # the next poll keeps saying LIVE while ticks are recent, with the venue's bar and the newer streamed close
    h._poll_market()
    assert [d for t, d in events][-1]["status"] == "LIVE"
    # a tick in the next minute starts a new forming bar
    h._on_tick(Tick("TEST:ABC", 100.4, bar_ts + 60_000 + 1_000))
    h.subscriptions[("TEST:ABC", "1m")]["sent_at"] = 0
    h._on_tick(Tick("TEST:ABC", 100.5, bar_ts + 60_000 + 2_000))
    assert [d for t, d in events][-1]["bar"][:2] == [bar_ts + 60_000, 100.4]


def test_stale_and_unavailable_are_labelled_and_block_bots(host):
    h, prov = host
    events = []
    h.bus.subscribe(lambda e: events.append(e["data"]) if e["topic"] == "candle" else None)
    h.subscribe("TEST:ABC", "1m")
    h._poll_market()
    prov.fail = True  # the venue stops answering: cached bars only
    from omos.errors import ProviderError

    def down(inst):
        raise ProviderError("TEST provider down")
    prov.fetch_quote = down
    h.market._live.clear()
    h._poll_market()
    assert events[-1]["status"] == "STALE" and events[-1]["stale"] is True
    q = h.market.get_quote("TEST:ABC")
    assert q.status == DataStatus.HISTORICAL and "cache" in q.source  # never passed off as live
    c = h.market.get_candles("TEST:ABC", "1m", limit=50)
    assert c.quality.get("stale")  # a bot refuses to act on this (see test_layered / BotRunner)


def test_live_quote_expires_after_15_seconds(host, monkeypatch):
    h, _ = host
    h.market.on_tick("TEST:ABC", 99.0, 1)
    assert h.market.live_quote("TEST:ABC").status == DataStatus.LIVE
    from omos.marketdata import service
    real = service.now_ms
    monkeypatch.setattr(service, "now_ms", lambda: real() + 16_000)
    assert h.market.live_quote("TEST:ABC") is None  # falls back to polling, which says how old it is


@pytest.mark.skipif(os.environ.get("OMOS_RUN_NETWORK") != "1", reason="network test; set OMOS_RUN_NETWORK=1 to run")
def test_real_streams_from_kraken_and_coinbase():
    got: dict[str, Tick] = {}
    lock = threading.Lock()

    def on_tick(t: Tick):
        with lock:
            got.setdefault(t.instrument_id, t)

    m = StreamManager(on_tick)
    m.start()
    try:
        m.want("KRAKEN:XBTUSD")
        m.want("COINBASE:BTC-USD")
        end = time.time() + 30
        while len(got) < 2 and time.time() < end:
            time.sleep(0.2)
    finally:
        m.stop()
    assert set(got) == {"KRAKEN:XBTUSD", "COINBASE:BTC-USD"}, m.status()
    a, b = got["KRAKEN:XBTUSD"].price, got["COINBASE:BTC-USD"].price
    assert abs(a / b - 1) < 0.02  # two venues, same asset: prices agree within 2%

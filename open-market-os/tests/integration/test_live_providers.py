"""Smoke tests against the real, free data sources. Run with OMOS_RUN_NETWORK=1.

These prove the providers work end-to-end against today's APIs. They can fail
for reasons outside this project (outages, regional blocks, API changes).
"""

import pytest

from omos.marketdata.models import DataStatus
from omos.marketdata.service import MarketDataService

pytestmark = pytest.mark.network


@pytest.fixture
def svc(db):
    return MarketDataService(db)


@pytest.mark.parametrize("iid,tf", [
    ("OKX:BTC-USDT", "1m"),
    ("COINBASE:BTC-USD", "1h"),
    ("KRAKEN:XBTUSD", "15m"),
    ("BINANCE:BTCUSDT", "5m"),
])
def test_crypto_venues(svc, iid, tf):
    c = svc.get_candles(iid, tf, limit=120)
    assert len(c) >= 100
    assert c.status == DataStatus.REALTIME
    assert (c.high >= c.low).all()
    assert c.quality["score"] > 90


def test_okx_deep_history_pages(svc):
    c = svc.get_candles("OKX:ETH-USDT", "1h", limit=1000)
    assert len(c) >= 990


def test_yahoo_equity_is_labelled_delayed(svc):
    c = svc.get_candles("YAHOO:AAPL", "1d", limit=60)
    assert len(c) >= 40
    assert c.status == DataStatus.DELAYED
    inst = svc.instrument("YAHOO:AAPL")
    assert inst.session == "xnys"


def test_yahoo_indian_equity(svc):
    c = svc.get_candles("YAHOO:RELIANCE.NS", "1d", limit=30)
    assert len(c) >= 15
    assert svc.instrument("YAHOO:RELIANCE.NS").session == "xnse"


def test_yahoo_gold_futures_and_forex(svc):
    assert len(svc.get_candles("YAHOO:GC=F", "1d", limit=20)) >= 10
    assert len(svc.get_candles("YAHOO:EURUSD=X", "1d", limit=20)) >= 10


def test_fred_series(svc):
    c = svc.get_candles("FRED:DGS10", "1d", limit=100)
    assert len(c) >= 90
    assert c.status == DataStatus.HISTORICAL


def test_ecb_rates(svc):
    c = svc.get_candles("ECB:EURUSD", "1d", limit=20)
    assert len(c) >= 10 and 0.5 < c.close[-1] < 2.0


def test_quotes(svc):
    q = svc.get_quote("OKX:BTC-USDT")
    assert q.price > 0 and q.status == DataStatus.REALTIME
    q2 = svc.get_quote("YAHOO:MSFT")
    assert q2.status == DataStatus.DELAYED


def test_search_across_providers(svc):
    r = svc.search("ETH", providers=["OKX", "COINBASE"])
    ids = {x["id"] for x in r["results"]}
    assert any(i.startswith("OKX:ETH") for i in ids)
    assert any(i.startswith("COINBASE:ETH") for i in ids)

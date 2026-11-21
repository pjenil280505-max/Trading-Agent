"""Shared fakes for tests: a controllable provider and a fully wired trading stack."""

from __future__ import annotations

from dataclasses import dataclass

import httpx
import numpy as np

from omos.alerts.service import AlertService, NotificationService
from omos.bots.service import BotRunner, BotService
from omos.events import EventBus
from omos.execution.gateway import ExecutionGateway
from omos.marketdata.models import AssetClass, Candles, DataStatus, Instrument, Quote
from omos.marketdata.providers.base import Provider, ProviderInfo
from omos.marketdata.service import MarketDataService
from omos.portfolio.service import PortfolioService, PriceBook
from omos.risk.engine import RiskEngine
from omos.screener.service import ScreenerService, WatchlistService


class ControlledProvider(Provider):
    """Serves an in-memory series per symbol; tests append bars to simulate time passing."""

    name = "TEST"
    rate_per_s = 10_000
    burst = 10_000

    def __init__(self, step_ms: int = 60_000, start_ms: int = 1_700_000_000_000):
        super().__init__(client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))))
        self.step = step_ms
        self.start = start_ms
        self.series: dict[str, list[list[float]]] = {}
        self.open_bar: dict[str, bool] = {}
        self.fail = False

    def set_closes(self, sym: str, closes, *, spread: float = 0.2, open_last: bool = False) -> None:
        rows = []
        prev = float(closes[0])
        for i, c in enumerate(closes):
            c = float(c)
            o = prev
            rows.append([self.start + i * self.step, o, max(o, c) + spread, min(o, c) - spread, c, 100.0])
            prev = c
        self.series[sym] = rows
        self.open_bar[sym] = open_last

    def append(self, sym: str, close: float, *, low: float | None = None, high: float | None = None) -> None:
        rows = self.series[sym]
        o = rows[-1][4]
        ts = rows[-1][0] + self.step
        rows.append([ts, o, high if high is not None else max(o, close) + 0.2,
                     low if low is not None else min(o, close) - 0.2, close, 100.0])

    def info(self):
        return ProviderInfo("TEST", "Test", ["test"], DataStatus.REALTIME, False, "", "", ["1m"], "", "", "", True)

    def native_timeframes(self):
        return ["1m"]

    def instrument(self, sym):
        return Instrument(f"TEST:{sym}", "TEST", sym, AssetClass.CRYPTO, name=sym, session="24x7")

    def search(self, q, limit=20):
        return [self.instrument(s) for s in self.series if q.upper() in s][:limit]

    def fetch_candles(self, inst, tf, *, start_ms=None, end_ms=None, limit=500):
        from omos.errors import ProviderError
        if self.fail:
            raise ProviderError("TEST provider down")
        rows = [list(r) for r in self.series[inst.provider_symbol]]
        if start_ms is not None:
            rows = [r for r in rows if r[0] >= start_ms]
        rows = rows[-limit:]
        return Candles.from_rows(rows, instrument_id=inst.id, timeframe=tf, status=DataStatus.REALTIME, source="TEST",
                                 fetched_at=1, last_closed=not self.open_bar.get(inst.provider_symbol, False))

    def fetch_quote(self, inst):
        last = self.series[inst.provider_symbol][-1]
        return Quote(inst.id, last[4], int(last[0]), DataStatus.REALTIME, "TEST")


@dataclass
class Stack:
    db: object
    provider: ControlledProvider
    market: MarketDataService
    prices: PriceBook
    portfolio: PortfolioService
    risk: RiskEngine
    gateway: ExecutionGateway
    bus: EventBus
    bots: BotService
    runner: BotRunner
    notifier: NotificationService
    alerts: AlertService
    screener: ScreenerService
    watchlists: WatchlistService
    quotes: dict


def make_stack(db, *, closes=None) -> Stack:
    prov = ControlledProvider()
    prov.set_closes("ABC", closes if closes is not None else np.linspace(100, 110, 300))
    market = MarketDataService(db, providers={"TEST": prov})
    prices = PriceBook()
    portfolio = PortfolioService(db, prices)
    risk = RiskEngine(db)
    bus = EventBus()
    quotes: dict = {}

    def quote_fn(iid: str) -> dict:
        if iid in quotes:
            return quotes[iid]
        q = market.get_quote(iid)
        return {"price": q.price, "status": q.status.value, "bid": q.bid, "ask": q.ask, "session_open": True}

    gateway = ExecutionGateway(db, risk, portfolio, prices, quote_fn, publish=bus.publish)
    bots = BotService(db, market, gateway, risk, bus)
    runner = BotRunner(bots, tick_s=0.05)
    notifier = NotificationService(db, bus)
    alerts = AlertService(db, market, notifier)
    return Stack(db, prov, market, prices, portfolio, risk, gateway, bus, bots, runner, notifier, alerts,
                 ScreenerService(db, market), WatchlistService(db), quotes)

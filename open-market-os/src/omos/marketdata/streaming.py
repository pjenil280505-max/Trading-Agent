"""Live prices over the venues' public WebSocket feeds (no account or key needed).

For instruments someone is looking at (a chart, a bot, a watchlist), the host keeps one connection
per venue, receives every trade or ticker update, and feeds it into the forming candle and the
latest quote. Those prices are labelled LIVE. If a stream drops, the regular polling (labelled LIVE
with its polling interval) continues on its own, and the stream reconnects with back-off.

Venues: Coinbase (ticker), Kraken (ticker, v2), Binance (trades; falls back to Binance's public data
mirror where the main endpoint is blocked) and OKX (trades).
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class Tick:
    instrument_id: str
    price: float
    ts: int                     # venue time in ms (receive time when the venue sends none)
    size: float = 0.0
    bid: float | None = None
    ask: float | None = None


class Venue:
    provider = ""
    urls: list[str] = []

    def stream_symbol(self, provider_symbol: str) -> str:
        return provider_symbol

    def subscribe(self, symbols: list[str]) -> list[str]:
        raise NotImplementedError

    def parse(self, raw: str | bytes, by_symbol: dict[str, str]) -> list[Tick]:
        raise NotImplementedError

    def ping(self) -> str | None:
        return None

    def url(self, symbols: list[str], attempt: int) -> str:
        return self.urls[attempt % len(self.urls)]


def _ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)


def _f(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x and x > 0 else None


class Coinbase(Venue):
    provider = "COINBASE"
    urls = ["wss://ws-feed.exchange.coinbase.com"]

    def subscribe(self, symbols):
        return [json.dumps({"type": "subscribe", "product_ids": symbols, "channels": ["ticker"]})]

    def parse(self, raw, by_symbol):
        m = json.loads(raw)
        if m.get("type") != "ticker" or m.get("product_id") not in by_symbol:
            return []
        px = _f(m.get("price"))
        if px is None:
            return []
        ts = _ms(m["time"]) if m.get("time") else int(time.time() * 1000)
        return [Tick(by_symbol[m["product_id"]], px, ts, _f(m.get("last_size")) or 0.0, _f(m.get("best_bid")),
                     _f(m.get("best_ask")))]


class Kraken(Venue):
    provider = "KRAKEN"
    urls = ["wss://ws.kraken.com/v2"]
    _CODES = {"XBT": "BTC", "XDG": "DOGE"}
    _QUOTES = ("USDT", "USDC", "USD", "EUR", "GBP", "JPY", "CAD", "CHF", "AUD", "XBT", "BTC", "ETH", "DAI")

    def stream_symbol(self, provider_symbol):
        s = provider_symbol.replace("/", "")
        for q in self._QUOTES:
            if s.endswith(q) and len(s) > len(q):
                base, quote = s[: -len(q)], q
                break
        else:
            base, quote = s[:-3], s[-3:]
        return f"{self._CODES.get(base, base)}/{self._CODES.get(quote, quote)}"

    def subscribe(self, symbols):
        return [json.dumps({"method": "subscribe", "params": {"channel": "ticker", "symbol": symbols}})]

    def parse(self, raw, by_symbol):
        m = json.loads(raw)
        if m.get("channel") != "ticker":
            return []
        out = []
        now = int(time.time() * 1000)
        for d in m.get("data") or []:
            iid = by_symbol.get(d.get("symbol"))
            px = _f(d.get("last"))
            if iid and px is not None:
                out.append(Tick(iid, px, now, 0.0, _f(d.get("bid")), _f(d.get("ask"))))
        return out


class Binance(Venue):
    provider = "BINANCE"
    urls = ["wss://stream.binance.com:9443", "wss://data-stream.binance.vision"]

    def stream_symbol(self, provider_symbol):
        return provider_symbol.replace("-", "").replace("/", "").lower()

    def url(self, symbols, attempt):
        return f"{self.urls[attempt % len(self.urls)]}/stream?streams=" + "/".join(f"{s}@trade" for s in symbols)

    def subscribe(self, symbols):
        return []  # combined stream: the symbols are in the URL

    def parse(self, raw, by_symbol):
        m = json.loads(raw)
        d = m.get("data") or m
        if d.get("e") != "trade":
            return []
        iid = by_symbol.get(str(d.get("s", "")).lower())
        px = _f(d.get("p"))
        return [Tick(iid, px, int(d.get("T") or time.time() * 1000), _f(d.get("q")) or 0.0)] if iid and px else []


class OKX(Venue):
    provider = "OKX"
    urls = ["wss://ws.okx.com:8443/ws/v5/public"]

    def subscribe(self, symbols):
        return [json.dumps({"op": "subscribe", "args": [{"channel": "trades", "instId": s} for s in symbols]})]

    def ping(self):
        return "ping"  # OKX closes connections that are silent for 30 s

    def parse(self, raw, by_symbol):
        if raw in ("pong", b"pong"):
            return []
        m = json.loads(raw)
        if (m.get("arg") or {}).get("channel") != "trades":
            return []
        out = []
        for d in m.get("data") or []:
            iid, px = by_symbol.get(d.get("instId")), _f(d.get("px"))
            if iid and px is not None:
                out.append(Tick(iid, px, int(d.get("ts") or time.time() * 1000), _f(d.get("sz")) or 0.0))
        return out


VENUES: dict[str, Venue] = {v.provider: v for v in (Coinbase(), Kraken(), Binance(), OKX())}


class StreamManager:
    """Keeps one WebSocket per venue for the instruments currently wanted. Runs its own asyncio loop."""

    def __init__(self, on_tick: Callable[[Tick], None], *, connect=None, max_backoff_s: float = 60.0):
        self.on_tick = on_tick
        self._wanted: dict[str, set[str]] = {}          # provider -> provider symbols
        self._ids: dict[str, dict[str, str]] = {}       # provider -> stream symbol -> instrument id
        self._state: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._changed: dict[str, asyncio.Event] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._connect = connect
        self.max_backoff = max_backoff_s
        self.enabled = True

    # -- what to stream --------------------------------------------------------------------------
    def supported(self, provider: str) -> bool:
        return provider in VENUES

    def want(self, instrument_id: str) -> None:
        provider, sym = instrument_id.split(":", 1)
        venue = VENUES.get(provider)
        if not venue or not self.enabled:
            return
        with self._lock:
            ss = venue.stream_symbol(sym)
            if ss in self._ids.setdefault(provider, {}):
                return
            self._ids[provider][ss] = instrument_id
            self._wanted.setdefault(provider, set()).add(ss)
        self._poke(provider)

    def unwant(self, instrument_id: str) -> None:
        provider, sym = instrument_id.split(":", 1)
        venue = VENUES.get(provider)
        if not venue:
            return
        with self._lock:
            ss = venue.stream_symbol(sym)
            self._ids.get(provider, {}).pop(ss, None)
            self._wanted.get(provider, set()).discard(ss)
        self._poke(provider)

    def _poke(self, provider: str) -> None:
        if self._loop and provider in self._changed:
            self._loop.call_soon_threadsafe(self._changed[provider].set)
        elif self._loop:
            self._loop.call_soon_threadsafe(self._spawn, provider)

    # -- lifecycle -----------------------------------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        ready = threading.Event()

        def run() -> None:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            for p in list(self._wanted):
                self._spawn(p)
            ready.set()
            try:
                self._loop.run_forever()
            finally:
                self._loop.close()

        self._thread = threading.Thread(target=run, name="omos-streams", daemon=True)
        self._thread.start()
        ready.wait(5)

    def stop(self) -> None:
        self._stop.set()
        if self._loop:
            def shutdown() -> None:
                for t in asyncio.all_tasks(self._loop):
                    t.cancel()
                self._loop.call_later(0.2, self._loop.stop)
            self._loop.call_soon_threadsafe(shutdown)
        if self._thread:
            self._thread.join(5)

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {p: {**st, "symbols": sorted(self._wanted.get(p, set()))} for p, st in self._state.items()}

    # -- connections -----------------------------------------------------------------------------------
    def _spawn(self, provider: str) -> None:
        if provider in self._changed:
            self._changed[provider].set()
            return
        self._changed[provider] = asyncio.Event()
        self._loop.create_task(self._run_venue(provider))  # type: ignore[union-attr]

    async def _open(self, url: str):
        if self._connect:
            return await self._connect(url)
        import websockets
        return await websockets.connect(url, open_timeout=10, ping_interval=20, ping_timeout=20, max_size=2 ** 22)

    async def _run_venue(self, provider: str) -> None:
        venue = VENUES[provider]
        attempt, backoff = 0, 1.0
        st = self._state.setdefault(provider, {"connected": False, "last_message": None, "errors": 0, "url": None})
        while not self._stop.is_set():
            with self._lock:
                symbols = sorted(self._wanted.get(provider, set()))
                by_symbol = dict(self._ids.get(provider, {}))
            changed = self._changed[provider]
            if not symbols:
                changed.clear()
                await changed.wait()
                continue
            url = venue.url(symbols, attempt)
            try:
                ws = await self._open(url)
                st.update(connected=True, url=url.split("?")[0], since=int(time.time() * 1000))
                for msg in venue.subscribe(symbols):
                    await ws.send(msg)
                changed.clear()
                backoff = 1.0
                last_ping = time.monotonic()
                while not self._stop.is_set() and not changed.is_set():
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=5)
                    except asyncio.TimeoutError:
                        raw = None
                    if venue.ping() and time.monotonic() - last_ping > 20:
                        await ws.send(venue.ping())
                        last_ping = time.monotonic()
                    if raw is None:
                        continue
                    st["last_message"] = int(time.time() * 1000)
                    try:
                        ticks = venue.parse(raw, by_symbol)
                    except (ValueError, KeyError, TypeError):
                        continue
                    for t in ticks:
                        try:
                            self.on_tick(t)
                        except Exception:  # noqa: BLE001 - one bad consumer must not kill the stream
                            log.exception("tick consumer failed")
                await ws.close()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - network: reconnect with back-off
                st.update(connected=False, last_error=f"{type(exc).__name__}: {exc}"[:200])
                st["errors"] = st.get("errors", 0) + 1
                attempt += 1
                log.info("%s stream: %s; reconnecting in %.0f s", provider, exc, backoff)
                try:
                    await asyncio.wait_for(changed.wait(), timeout=backoff)
                except asyncio.TimeoutError:
                    pass
                backoff = min(self.max_backoff, backoff * 2)
                continue
            st["connected"] = False

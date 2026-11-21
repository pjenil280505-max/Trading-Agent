"""In-process publish/subscribe bus.

Producers (gateway, bots, alerts, risk) publish from any thread. WebSocket clients and
the workflow engine subscribe. Subscribers must not block; slow consumers are dropped
rather than allowed to stall trading.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections import deque
from collections.abc import Callable
from typing import Any

from omos.util import now_ms

log = logging.getLogger(__name__)


class EventBus:
    def __init__(self, history: int = 500):
        self._subs: dict[int, Callable[[dict[str, Any]], None]] = {}
        self._next = 0
        self._lock = threading.Lock()
        self.recent: deque[dict[str, Any]] = deque(maxlen=history)

    def subscribe(self, fn: Callable[[dict[str, Any]], None]) -> int:
        with self._lock:
            self._next += 1
            self._subs[self._next] = fn
            return self._next

    def unsubscribe(self, token: int) -> None:
        with self._lock:
            self._subs.pop(token, None)

    def publish(self, topic: str, data: dict[str, Any]) -> None:
        evt = {"topic": topic, "ts": now_ms(), "data": data}
        self.recent.append(evt)
        with self._lock:
            subs = list(self._subs.items())
        for token, fn in subs:
            try:
                fn(evt)
            except Exception:  # a broken subscriber must never break the publisher
                log.exception("event subscriber %s failed; removing it", token)
                self.unsubscribe(token)

    def async_queue(self, loop: asyncio.AbstractEventLoop, maxsize: int = 1000) -> tuple[int, asyncio.Queue]:
        """A subscription that delivers into an asyncio queue owned by ``loop``."""
        q: asyncio.Queue = asyncio.Queue(maxsize=maxsize)

        def push(evt: dict[str, Any]) -> None:
            def put() -> None:
                if q.full():
                    try:
                        q.get_nowait()  # drop the oldest rather than block producers
                    except asyncio.QueueEmpty:
                        pass
                q.put_nowait(evt)
            loop.call_soon_threadsafe(put)

        return self.subscribe(push), q

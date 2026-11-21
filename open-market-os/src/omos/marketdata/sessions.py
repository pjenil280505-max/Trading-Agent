"""Market session calendars.

Regular trading hours only. Exchange holidays are NOT modelled (documented
limitation): on a holiday the calendar still reports the market as open, but no
bars will arrive, and gap detection reports the missing bars instead of hiding them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Session:
    id: str
    name: str
    tz: str
    open: time | None   # None = continuous (24h)
    close: time | None
    weekdays: tuple[int, ...]  # 0 = Monday
    note: str = ""

    def is_open(self, ts_ms: int) -> bool:
        if self.id == "24x7":
            return True
        if self.id == "fx24x5":
            dt = datetime.fromtimestamp(ts_ms / 1000, tz=ZoneInfo("America/New_York"))
            wd, t = dt.weekday(), dt.time()
            # Sunday 17:00 to Friday 17:00 New York time
            if wd == 6:
                return t >= time(17, 0)
            if wd == 4:
                return t < time(17, 0)
            return wd < 4
        dt = datetime.fromtimestamp(ts_ms / 1000, tz=ZoneInfo(self.tz))
        if dt.weekday() not in self.weekdays:
            return False
        assert self.open is not None and self.close is not None
        return self.open <= dt.time() < self.close

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "timezone": self.tz,
            "open": self.open.isoformat() if self.open else None,
            "close": self.close.isoformat() if self.close else None,
            "weekdays": list(self.weekdays),
            "note": self.note,
        }


_WEEK = (0, 1, 2, 3, 4)

SESSIONS: dict[str, Session] = {
    s.id: s
    for s in [
        Session("24x7", "Continuous (crypto)", "UTC", None, None, tuple(range(7))),
        Session("fx24x5", "Forex 24x5 (Sun 17:00 – Fri 17:00 New York)", "America/New_York", None, None, _WEEK),
        Session("xnys", "NYSE / NASDAQ regular hours", "America/New_York", time(9, 30), time(16, 0), _WEEK,
                "Holidays and half-days are not modelled."),
        Session("xnse", "NSE India regular hours", "Asia/Kolkata", time(9, 15), time(15, 30), _WEEK,
                "Holidays are not modelled."),
        Session("xlon", "London Stock Exchange", "Europe/London", time(8, 0), time(16, 30), _WEEK,
                "Holidays are not modelled."),
        Session("xtks", "Tokyo Stock Exchange", "Asia/Tokyo", time(9, 0), time(15, 30), _WEEK,
                "Lunch break and holidays are not modelled."),
        Session("xhkg", "Hong Kong Exchange", "Asia/Hong_Kong", time(9, 30), time(16, 0), _WEEK,
                "Lunch break and holidays are not modelled."),
        Session("xetr", "Xetra (Frankfurt)", "Europe/Berlin", time(9, 0), time(17, 30), _WEEK,
                "Holidays are not modelled."),
        Session("daily", "Daily publication (economic data)", "UTC", time(0, 0), time(23, 59, 59), tuple(range(7))),
    ]
}


def get_session(session_id: str) -> Session:
    return SESSIONS.get(session_id, SESSIONS["24x7"])


def market_state(session_id: str, ts_ms: int | None = None) -> str:
    ts = ts_ms if ts_ms is not None else int(datetime.now(timezone.utc).timestamp() * 1000)
    return "open" if get_session(session_id).is_open(ts) else "closed"

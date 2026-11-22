"""News aggregation from RSS feeds.

Sources: Yahoo Finance per-symbol headline RSS (unofficial) plus any RSS/Atom feeds the user
adds. Items are de-duplicated by normalised title, tagged with symbols and topics, and given a
*lexicon* sentiment score. That score is a crude word count, labelled ``lexicon-heuristic``;
it is not a judgement and not AI. AI sentiment, when used, is labelled ``ai-generated``.

All feed content is untrusted: XML is parsed with defusedxml (no entity expansion / XXE), and
text is stored as plain text (HTML stripped) and never executed or rendered as HTML.
"""

from __future__ import annotations

import hashlib
import html
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote, urlparse

import httpx
from defusedxml import DefusedXmlException
from defusedxml import ElementTree as ET

from omos.errors import OmosError, ValidationError
from omos.marketdata.providers.base import USER_AGENT
from omos.storage.db import Database
from omos.util import dumps, loads, now_ms

DEFAULT_FEEDS = [
    {"name": "Federal Reserve press releases", "url": "https://www.federalreserve.gov/feeds/press_all.xml"},
    {"name": "SEC press releases", "url": "https://www.sec.gov/news/pressreleases.rss"},
]
TOPICS = {
    "earnings": ["earnings", "eps", "revenue", "quarterly results", "guidance", "profit"],
    "macro": ["inflation", "cpi", "gdp", "jobs report", "payroll", "unemployment", "fomc", "interest rate", "fed "],
    "crypto": ["bitcoin", "ethereum", "crypto", "stablecoin", "blockchain", "etf flows"],
    "m&a": ["merger", "acquisition", "acquire", "buyout", "takeover"],
    "regulation": ["sec ", "regulator", "lawsuit", "antitrust", "probe", "settlement", "fine"],
    "commodities": ["oil", "crude", "gold", "silver", "opec", "natural gas"],
}
POS = {"beat", "beats", "surge", "surges", "soar", "soars", "rally", "rallies", "gain", "gains", "record", "upgrade",
       "upgraded", "growth", "strong", "rise", "rises", "jump", "jumps", "profit", "bullish", "outperform", "approval"}
NEG = {"miss", "misses", "plunge", "plunges", "drop", "drops", "fall", "falls", "slump", "loss", "losses", "downgrade",
       "downgraded", "weak", "cut", "cuts", "lawsuit", "probe", "bearish", "recall", "warning", "fraud", "bankrupt", "halt"}
_TAG = re.compile(r"<[^>]+>")


def strip_html(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAG.sub(" ", s or "")).replace("\xa0", " ")).strip()


def lexicon_sentiment(text: str) -> float:
    words = re.findall(r"[a-z]+", text.lower())
    p = sum(w in POS for w in words)
    n = sum(w in NEG for w in words)
    return 0.0 if p + n == 0 else round((p - n) / (p + n), 3)


def parse_feed(xml_bytes: bytes, source: str) -> list[dict[str, Any]]:
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise OmosError(f"{source}: feed is not valid XML") from exc
    except DefusedXmlException as exc:  # DTD / entity tricks (XXE, entity expansion): refuse this feed only
        raise OmosError(f"{source}: feed uses forbidden XML features ({type(exc).__name__})") from exc
    items = []
    ns_atom = "{http://www.w3.org/2005/Atom}"
    for it in list(root.iter("item")) + list(root.iter(f"{ns_atom}entry")):
        title = (it.findtext("title") or it.findtext(f"{ns_atom}title") or "").strip()
        if not title:
            continue
        link = it.findtext("link") or ""
        if not link:
            el = it.find(f"{ns_atom}link")
            link = el.get("href", "") if el is not None else ""
        desc = it.findtext("description") or it.findtext(f"{ns_atom}summary") or ""
        when = it.findtext("pubDate") or it.findtext(f"{ns_atom}updated") or it.findtext(f"{ns_atom}published") or ""
        items.append({"title": strip_html(title)[:400], "url": link.strip()[:1000] if link.startswith(("http://", "https://")) else "",
                      "summary": strip_html(desc)[:1500], "ts": _parse_time(when), "source": source})
    return items


def _parse_time(s: str) -> int:
    s = (s or "").strip()
    if not s:
        return now_ms()
    try:
        return int(parsedate_to_datetime(s).timestamp() * 1000)
    except (TypeError, ValueError):
        pass
    try:
        return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return now_ms()


class NewsService:
    def __init__(self, db: Database, client: httpx.Client | None = None):
        self.db = db
        self.client = client or httpx.Client(timeout=15, follow_redirects=True,
                                             headers={"User-Agent": USER_AGENT + " news-reader",
                                                      "Accept": "application/rss+xml, application/xml, text/xml"})

    def feeds(self) -> list[dict[str, Any]]:
        return self.db.get_setting("news.feeds", DEFAULT_FEEDS)

    def set_feeds(self, feeds: list[dict[str, Any]]) -> list[dict[str, Any]]:
        clean = []
        for f in feeds[:50]:
            url = str(f.get("url", "")).strip()
            p = urlparse(url)
            if p.scheme != "https" or not p.hostname:
                raise ValidationError(f"feed URLs must be https:// ({url!r})")
            clean.append({"name": str(f.get("name") or p.hostname)[:80], "url": url[:500]})
        self.db.set_setting("news.feeds", clean)
        return clean

    def _fetch(self, url: str, source: str) -> list[dict[str, Any]]:
        try:
            r = self.client.get(url)
        except httpx.HTTPError as exc:
            raise OmosError(f"{source}: {type(exc).__name__}") from exc
        if r.status_code != 200:
            raise OmosError(f"{source}: HTTP {r.status_code}")
        if len(r.content) > 5_000_000:
            raise OmosError(f"{source}: feed too large")
        return parse_feed(r.content, source)

    def refresh(self, symbols: list[str] | None = None) -> dict[str, Any]:
        found, errors = [], {}
        for sym in (symbols or [])[:30]:
            ticker = sym.split(":", 1)[-1]
            try:
                items = self._fetch(f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={quote(ticker, safe='')}&region=US&lang=en-US",
                                    "Yahoo Finance (unofficial RSS)")
                for i in items:
                    i["symbols"] = [sym]
                found += items
            except OmosError as exc:
                errors[sym] = exc.message
        for f in self.feeds():
            try:
                found += self._fetch(f["url"], f["name"])
            except OmosError as exc:
                errors[f["name"]] = exc.message
        stored = self.store(found, symbols or [])
        return {"fetched": len(found), "new": stored, "errors": errors}

    def store(self, items: list[dict[str, Any]], known_symbols: list[str]) -> int:
        new = 0
        for it in items:
            # de-duplication key, not a security use
            key = hashlib.sha1(re.sub(r"\W+", " ", it["title"].lower()).strip().encode(), usedforsecurity=False).hexdigest()
            text = f"{it['title']} {it.get('summary', '')}"
            low = text.lower()
            topics = [t for t, kws in TOPICS.items() if any(k in low for k in kws)]
            syms = set(it.get("symbols", []))
            for s in known_symbols:
                t = s.split(":", 1)[-1].split("-")[0].split(".")[0]
                if len(t) >= 3 and re.search(rf"\b{re.escape(t)}\b", text):
                    syms.add(s)
            existing = self.db.query_one("SELECT symbols FROM news_items WHERE id = ?", (key,))
            if existing:
                merged = sorted(set(loads(existing["symbols"], [])) | syms)
                self.db.execute("UPDATE news_items SET symbols = ? WHERE id = ?", (dumps(merged), key))
                continue
            self.db.execute(
                "INSERT INTO news_items(id, ts, source, title, url, summary, symbols, topics, sentiment, sentiment_method, fetched_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (key, it["ts"], it["source"], it["title"], it.get("url", ""), it.get("summary", ""), dumps(sorted(syms)),
                 dumps(topics), lexicon_sentiment(text), "lexicon-heuristic", now_ms()))
            new += 1
        return new

    def search(self, *, q: str = "", symbol: str | None = None, topic: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        sql = "SELECT * FROM news_items WHERE 1=1"
        params: list[Any] = []
        if q:
            sql += " AND (title LIKE ? OR summary LIKE ?)"
            params += [f"%{q[:80]}%", f"%{q[:80]}%"]
        if symbol:
            sql += " AND symbols LIKE ?"
            params.append(f'%"{symbol}"%')
        if topic:
            sql += " AND topics LIKE ?"
            params.append(f'%"{topic}"%')
        rows = self.db.query(sql + " ORDER BY ts DESC LIMIT ?", (*params, min(limit, 500)))
        for r in rows:
            r["symbols"] = loads(r["symbols"], [])
            r["topics"] = loads(r["topics"], [])
            r["time"] = datetime.fromtimestamp(r["ts"] / 1000, tz=timezone.utc).isoformat()
        return rows

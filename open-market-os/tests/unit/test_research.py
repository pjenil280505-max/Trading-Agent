"""News (RSS), SEC fundamentals and economic-calendar logic, with HTTP mocked so results are exact."""

import json

import httpx
import pytest

from omos.errors import NotFound, OmosError, ValidationError
from omos.research.calendar import _transform
from omos.research.fundamentals import FundamentalsService
from omos.research.news import NewsService, lexicon_sentiment, parse_feed

RSS = b"""<?xml version="1.0"?>
<rss version="2.0"><channel><title>t</title>
<item><title>ACME &lt;b&gt;beats&lt;/b&gt; earnings, shares surge</title><link>https://example.com/a</link>
  <description>&lt;p&gt;Quarterly revenue &amp;amp; profit up&lt;/p&gt;</description><pubDate>Tue, 10 Nov 2026 14:30:00 GMT</pubDate></item>
<item><title>Regulator opens probe into ACME</title><link>javascript:alert(1)</link><pubDate>bad date</pubDate></item>
<item><title></title><link>https://example.com/empty</link></item>
</channel></rss>"""

ATOM = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>f</title>
<entry><title>ACME beats earnings, shares SURGE!</title><link href="https://example.org/dup"/><updated>2026-11-10T15:00:00Z</updated>
<summary>same story, other outlet</summary></entry>
<entry><title>Oil prices slump as OPEC cuts forecast</title><link href="https://example.org/oil"/><updated>2026-11-11T09:00:00Z</updated></entry>
</feed>"""

XXE = b"""<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]><rss><channel><item><title>&x;</title></item></channel></rss>"""


def test_parse_rss_strips_html_drops_unsafe_links_and_empty_items():
    items = parse_feed(RSS, "src")
    assert [i["title"] for i in items] == ["ACME beats earnings, shares surge", "Regulator opens probe into ACME"]
    assert items[0]["summary"] == "Quarterly revenue & profit up" and items[0]["url"] == "https://example.com/a"
    assert items[0]["ts"] == 1794321000000
    assert items[1]["url"] == ""                      # javascript: links are never kept


def test_parse_atom():
    items = parse_feed(ATOM, "atom")
    assert items[0]["url"] == "https://example.org/dup" and items[0]["ts"] == 1794322800000


def test_entity_declarations_are_refused_as_a_feed_error():
    """A hostile feed (XXE / entity expansion) must be a normal per-feed error, not a crash."""
    with pytest.raises(OmosError):
        parse_feed(XXE, "evil")


def test_lexicon_sentiment_is_a_bounded_word_count():
    assert lexicon_sentiment("shares surge after record profit") == 1.0
    assert lexicon_sentiment("stock falls on fraud probe") == -1.0
    assert lexicon_sentiment("company holds meeting") == 0.0


def _news(db, routes):
    seen = []

    def handler(req: httpx.Request):
        seen.append(str(req.url))
        for key, body in routes.items():
            if key in str(req.url):
                return httpx.Response(200, content=body) if isinstance(body, bytes) else httpx.Response(body)
        return httpx.Response(404)

    return NewsService(db, client=httpx.Client(transport=httpx.MockTransport(handler))), seen


def test_refresh_dedupes_tags_and_isolates_failing_feeds(db):
    n, seen = _news(db, {"feeds.finance.yahoo.com": RSS, "example.org/feed": ATOM, "example.org/evil": XXE,
                         "example.org/down": 503})
    n.set_feeds([{"name": "Atom", "url": "https://example.org/feed"}, {"name": "Evil", "url": "https://example.org/evil"},
                 {"name": "Down", "url": "https://example.org/down"}])
    r = n.refresh(["YAHOO:GC=F"])
    assert "s=GC%3DF" in seen[0]                       # symbols are URL-encoded into the query
    assert r["fetched"] == 4 and r["new"] == 3          # the "beats earnings" story appears twice -> one item
    assert set(r["errors"]) == {"Evil", "Down"}
    acme = [i for i in n.search(q="ACME") if "beats" in i["title"]]
    assert len(acme) == 1 and acme[0]["symbols"] == ["YAHOO:GC=F"] and "earnings" in acme[0]["topics"]
    assert acme[0]["sentiment"] == 1.0 and acme[0]["sentiment_method"] == "lexicon-heuristic"
    assert [i["title"] for i in n.search(topic="commodities")] == ["Oil prices slump as OPEC cuts forecast"]
    assert len(n.search(symbol="YAHOO:GC=F")) == 2


def test_feed_urls_must_be_https(db):
    n, _ = _news(db, {})
    with pytest.raises(ValidationError):
        n.set_feeds([{"name": "x", "url": "http://example.org/feed"}])


# ---------------------------------------------------------------- SEC fundamentals
def _fact(val, end, fy, fp, form, filed, start=None):
    f = {"val": val, "end": end, "fy": fy, "fp": fp, "form": form, "filed": filed}
    if start:
        f["start"] = start
    return f


FACTS = {
    "entityName": "ACME CORP",
    "facts": {"us-gaap": {
        "Revenues": {"units": {"USD": [
            _fact(102.0, "2023-09-30", 2024, "FY", "10-K", "2024-11-01", "2022-10-01"),   # restated comparative, listed FIRST
            _fact(100.0, "2023-09-30", 2023, "FY", "10-K", "2023-11-03", "2022-10-01"),
            _fact(120.0, "2024-09-28", 2024, "FY", "10-K", "2024-11-01", "2023-10-01"),
            _fact(30.0, "2023-12-30", 2024, "Q1", "10-Q", "2024-02-01", "2023-10-01"),
            _fact(400.0, "2024-09-28", 2024, "FY", "10-K", "2024-11-01", "2021-10-01"),   # 3-year span: ignored
        ]}},
        "NetIncomeLoss": {"units": {"USD": [
            _fact(10.0, "2023-09-30", 2023, "FY", "10-K", "2023-11-03", "2022-10-01"),
            _fact(18.0, "2024-09-28", 2024, "FY", "10-K", "2024-11-01", "2023-10-01")]}},
        "StockholdersEquity": {"units": {"USD": [
            _fact(50.0, "2023-09-30", 2023, "FY", "10-K", "2023-11-03"),
            _fact(60.0, "2024-09-28", 2024, "FY", "10-K", "2024-11-01")]}},
        "EarningsPerShareDiluted": {"units": {"USD/shares": [
            _fact(1.5, "2024-09-28", 2024, "FY", "10-K", "2024-11-01", "2023-10-01")]}},
    }},
}


def _sec(db, facts_status=200):
    seen = []

    def handler(req: httpx.Request):
        seen.append(req)
        if req.url.path.endswith("company_tickers.json"):
            return httpx.Response(200, json={"0": {"cik_str": 320193, "ticker": "ACME", "title": "Acme Corp"}})
        if "companyfacts/CIK0000320193.json" in req.url.path:
            return httpx.Response(facts_status, json=FACTS)
        return httpx.Response(404)

    return FundamentalsService(db, client=httpx.Client(transport=httpx.MockTransport(handler))), seen


def test_sec_requires_the_users_own_contact_email(db):
    svc, seen = _sec(db)
    with pytest.raises(ValidationError, match="contact e-mail"):
        svc.company("ACME")
    assert seen == []                                  # nothing is sent without it


def test_sec_company_facts_annual_quarterly_and_ratios(db):
    db.set_setting("sec.contact", "someone@example.com")
    svc, seen = _sec(db)
    c = svc.company("acme")
    assert seen[0].headers["User-Agent"].endswith("someone@example.com")
    assert c["name"] == "ACME CORP" and c["cik"] == 320193
    a = {r["period_end"]: r for r in c["annual"]}
    assert set(a) == {"2023-09-30", "2024-09-28"}
    assert a["2023-09-30"]["revenue"] == 102.0          # latest filed (restated) value is used
    assert a["2023-09-30"]["fiscal_year"] == 2023       # label from the original filing, not the comparative
    assert a["2024-09-28"]["revenue"] == 120.0 and a["2024-09-28"]["eps_diluted"] == 1.5
    assert [q["period_end"] for q in c["quarterly"]] == ["2023-12-30"]
    r = {x["period_end"]: x for x in c["ratios"]}
    assert r["2024-09-28"]["net_margin"] == pytest.approx(0.15)
    assert r["2024-09-28"]["roe"] == pytest.approx(0.3)
    assert r["2024-09-28"]["revenue_growth"] == pytest.approx(120 / 102 - 1)
    assert r["2023-09-30"]["revenue_growth"] is None


def test_sec_unknown_ticker_and_missing_facts(db):
    db.set_setting("sec.contact", "someone@example.com")
    svc, _ = _sec(db)
    with pytest.raises(NotFound):
        svc.company("NOPE")
    svc404, _ = _sec(db, facts_status=404)
    db.cache_set("sec:tickers", {"ACME": {"cik": 320193, "name": "Acme Corp"}}, ttl_s=60)
    with pytest.raises(NotFound):
        svc404.company("ACME")


# ---------------------------------------------------------------- economic calendar
def test_calendar_transforms():
    v = [100.0 + i for i in range(13)]
    assert _transform(v, "level")[-1] == 112.0
    assert _transform(v, "diff")[-1] == 1.0 and _transform(v, "diff")[0] is None
    assert _transform(v, "mom")[-1] == pytest.approx((112 / 111 - 1) * 100)
    assert _transform(v, "yoy")[-1] == pytest.approx(12.0) and _transform(v, "yoy")[11] is None
    assert _transform([100.0, 101.0], "qoq_ann")[-1] == pytest.approx((1.01 ** 4 - 1) * 100)
    assert json.dumps(_transform([0.0, 1.0], "mom")) == "[null, null]"   # no division by zero

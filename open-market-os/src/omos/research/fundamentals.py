"""Company fundamentals from SEC EDGAR XBRL "company facts" (US-listed companies only).

* Free, no key. The SEC asks every client to send a User-Agent with a contact e-mail and to
  stay under 10 requests/second (https://www.sec.gov/os/accessing-edgar-data). The contact is
  a user setting (Settings > Data sources > SEC contact); it is never filled in for you.
* Values are exactly what companies reported in their filings (10-K annual, 10-Q quarterly).
  Missing tags stay missing; nothing is estimated.
"""

from __future__ import annotations

from typing import Any

import httpx

from omos.errors import DataUnavailable, NotFound, ValidationError
from omos.marketdata.ratelimit import TokenBucket
from omos.storage.db import Database

# preferred XBRL tags per line item, in order
ITEMS: dict[str, list[str]] = {
    "revenue": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet",
                "RevenueFromContractWithCustomerIncludingAssessedTax"],
    "gross_profit": ["GrossProfit"],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "eps_diluted": ["EarningsPerShareDiluted"],
    "total_assets": ["Assets"],
    "total_liabilities": ["Liabilities"],
    "equity": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue"],
    "long_term_debt": ["LongTermDebtNoncurrent", "LongTermDebt"],
    "operating_cash_flow": ["NetCashProvidedByUsedInOperatingActivities"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment"],
    "dividends_paid": ["PaymentsOfDividends", "PaymentsOfDividendsCommonStock"],
    "shares_outstanding": ["CommonStockSharesOutstanding", "EntityCommonStockSharesOutstanding"],
}


class FundamentalsService:
    def __init__(self, db: Database, client: httpx.Client | None = None):
        self.db = db
        self.client = client or httpx.Client(timeout=20, follow_redirects=True)
        self.bucket = TokenBucket(5.0, 5.0)

    def _ua(self) -> str:
        contact = (self.db.get_setting("sec.contact") or "").strip()
        if not contact or "@" not in contact:
            raise ValidationError("SEC EDGAR requires a contact e-mail in the User-Agent. "
                                  "Add yours in Settings > Data sources > SEC contact.")
        return f"OpenMarketOS research {contact}"

    def _get(self, url: str) -> Any:
        if not self.bucket.acquire(timeout=10):
            raise DataUnavailable("SEC: local rate limit")
        try:
            r = self.client.get(url, headers={"User-Agent": self._ua(), "Accept": "application/json"})
        except httpx.HTTPError as exc:
            raise DataUnavailable(f"SEC EDGAR unreachable: {type(exc).__name__}") from exc
        if r.status_code == 404:
            raise NotFound("SEC has no XBRL data for this company")
        if r.status_code != 200:
            raise DataUnavailable(f"SEC EDGAR: HTTP {r.status_code}")
        return r.json()

    def cik(self, ticker: str) -> dict[str, Any]:
        t = ticker.strip().upper().split(":")[-1]
        if not t or len(t) > 12:
            raise ValidationError("invalid ticker")
        table = self.db.cache_get("sec:tickers")
        if table is None:
            raw = self._get("https://www.sec.gov/files/company_tickers.json")
            table = {v["ticker"].upper(): {"cik": int(v["cik_str"]), "name": v["title"]} for v in raw.values()}
            self.db.cache_set("sec:tickers", table, ttl_s=86_400)
        hit = table.get(t) or table.get(t.replace(".", "-"))
        if not hit:
            raise NotFound(f"{t} is not in the SEC company list (fundamentals cover US SEC filers only)")
        return {"ticker": t, **hit}

    def company(self, ticker: str) -> dict[str, Any]:
        info = self.cik(ticker)
        key = f"sec:facts:{info['cik']}"
        facts = self.db.cache_get(key)
        if facts is None:
            facts = self._get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{info['cik']:010d}.json")
            facts = {"entityName": facts.get("entityName"), "facts": facts.get("facts", {})}
            self.db.cache_set(key, facts, ttl_s=6 * 3600)
        annual = self._series(facts, forms=("10-K", "10-K/A", "20-F", "40-F"), fp="FY")
        quarterly = self._series(facts, forms=("10-Q", "10-Q/A"), fp=None)
        return {"ticker": info["ticker"], "cik": info["cik"], "name": facts.get("entityName") or info["name"],
                "source": "SEC EDGAR XBRL company facts (as filed)", "annual": annual, "quarterly": quarterly,
                "ratios": self.ratios(annual)}

    def _series(self, facts: dict[str, Any], *, forms: tuple[str, ...], fp: str | None) -> list[dict[str, Any]]:
        by_period: dict[str, dict[str, Any]] = {}
        gaap = facts.get("facts", {}).get("us-gaap", {})
        dei = facts.get("facts", {}).get("dei", {})
        for item, tags in ITEMS.items():
            for tag in tags:
                node = gaap.get(tag) or dei.get(tag)
                if not node:
                    continue
                units = node.get("units", {})
                unit_key = "USD" if "USD" in units else ("USD/shares" if "USD/shares" in units else next(iter(units), None))
                if not unit_key:
                    continue
                got = False
                for f in units[unit_key]:
                    if f.get("form") not in forms or (fp and f.get("fp") != fp):
                        continue
                    if fp is None and f.get("fp") not in ("Q1", "Q2", "Q3"):
                        continue
                    period = f.get("end")
                    if not period:
                        continue
                    # duration items: keep ~1-year (annual) or ~1-quarter spans only
                    if f.get("start"):
                        from datetime import date
                        d = (date.fromisoformat(f["end"]) - date.fromisoformat(f["start"])).days
                        if (fp == "FY" and not 330 <= d <= 380) or (fp is None and not 80 <= d <= 100):
                            continue
                    row = by_period.setdefault(period, {"period_end": period, "fiscal_year": f.get("fy"), "form": f.get("form")})
                    if item not in row or f.get("filed", "") > row.get(f"_{item}_filed", ""):
                        row[item] = f.get("val")
                        row[f"_{item}_filed"] = f.get("filed", "")
                    got = True
                if got:
                    break
        out = []
        for p in sorted(by_period)[-12:]:
            row = {k: v for k, v in by_period[p].items() if not k.startswith("_")}
            out.append(row)
        return out

    @staticmethod
    def ratios(annual: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        prev = None
        for r in annual:
            def div(a, b):
                x, y = r.get(a), r.get(b)
                return x / y if isinstance(x, int | float) and isinstance(y, int | float) and y else None

            fcf = (r["operating_cash_flow"] - r["capex"]) if isinstance(r.get("operating_cash_flow"), int | float) \
                and isinstance(r.get("capex"), int | float) else None
            growth = None
            if prev and isinstance(prev.get("revenue"), int | float) and prev["revenue"] and isinstance(r.get("revenue"), int | float):
                growth = r["revenue"] / prev["revenue"] - 1
            out.append({"period_end": r["period_end"], "gross_margin": div("gross_profit", "revenue"),
                        "operating_margin": div("operating_income", "revenue"), "net_margin": div("net_income", "revenue"),
                        "roe": div("net_income", "equity"), "debt_to_equity": div("long_term_debt", "equity"),
                        "liabilities_to_assets": div("total_liabilities", "total_assets"), "free_cash_flow": fcf,
                        "revenue_growth": growth})
            prev = r
        return out

"""Headlines for the daily digest: Finnhub market and NVIDIA news, Fed press releases."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from bottom.sources import USER_AGENT
from bottom.store import RawStore

FINNHUB_URL = "https://finnhub.io/api/v1"
FED_RSS = "https://www.federalreserve.gov/feeds/press_all.xml"


@dataclass(frozen=True)
class Headline:
    group: str  # "market", "nvda" or "fed"
    title: str
    url: str
    source: str
    published: datetime
    summary: str = ""


def fetch_headlines(
    api_key: str | None, today: date, store: RawStore | None = None, days: int = 3
) -> tuple[list[Headline], dict[str, str]]:
    """Return headlines from every source that answers, and the errors of those that do not."""
    since = datetime.combine(today - timedelta(days=days), datetime.min.time(), UTC)
    headlines: list[Headline] = []
    errors: dict[str, str] = {}

    if api_key:
        try:
            market = _finnhub("/news", {"category": "general"}, api_key)
            if store is not None:
                store.save_json("news_market.json", market)
            headlines += parse_finnhub(market, "market", since)[:30]
        except RuntimeError as exc:
            errors["news market"] = str(exc)
        try:
            params = {
                "symbol": "NVDA",
                "from": (today - timedelta(days=days)).isoformat(),
                "to": today.isoformat(),
            }
            nvda = _finnhub("/company-news", params, api_key)
            if store is not None:
                store.save_json("news_nvda.json", nvda)
            nvda_items = parse_finnhub(nvda, "nvda", since)
            # The 15 newest, plus any older ones about Huang: a busy news day can
            # otherwise push the catalyst story out of view.
            huang = [h for h in nvda_items[15:] if "huang" in f"{h.title} {h.summary}".lower()]
            headlines += nvda_items[:15] + huang[:10]
        except RuntimeError as exc:
            errors["news nvda"] = str(exc)
    else:
        errors["news"] = "FINNHUB_API_KEY is not set"

    try:
        response = httpx.get(
            FED_RSS, headers={"User-Agent": USER_AGENT}, timeout=30, follow_redirects=True
        )
        if response.status_code != 200:
            raise RuntimeError(f"HTTP {response.status_code}")
        if store is not None:
            store.save_bytes("fed_press.xml", response.content)
        fed_since = since - timedelta(days=11)  # Fed statements matter for longer
        headlines += parse_fed_rss(response.content, fed_since)[:8]
    except (httpx.HTTPError, RuntimeError, ET.ParseError) as exc:
        errors["fed press releases"] = str(exc) or type(exc).__name__

    return headlines, errors


def fetch_company_headlines(
    api_key: str | None,
    today: date,
    companies: dict[str, list[str]],
    per_company: int = 4,
    store: RawStore | None = None,
    days: int = 3,
) -> tuple[list[Headline], dict[str, str]]:
    """Recent news on a few large companies in each sector, grouped under the sector code."""
    if not api_key:
        return [], {}
    since = datetime.combine(today - timedelta(days=days), datetime.min.time(), UTC)
    params = {"from": (today - timedelta(days=days)).isoformat(), "to": today.isoformat()}
    headlines: list[Headline] = []
    failed: list[str] = []
    for line, symbols in companies.items():
        for symbol in symbols:
            try:
                rows = _finnhub("/company-news", {**params, "symbol": symbol}, api_key)
            except RuntimeError:
                failed.append(symbol)
                continue
            if store is not None:
                store.save_json(f"news_{symbol.lower()}.json", rows)
            headlines += parse_finnhub(rows, line, since)[:per_company]
    errors = {"news companies": f"no news for {', '.join(failed)}"} if failed else {}
    return dedupe(headlines), errors


def _finnhub(path: str, params: dict[str, str], api_key: str) -> Any:
    try:
        response = httpx.get(
            FINNHUB_URL + path, params=params, headers={"X-Finnhub-Token": api_key}, timeout=30
        )
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Finnhub {path}: {type(exc).__name__}") from None
    if response.status_code != 200:
        raise RuntimeError(f"Finnhub {path}: HTTP {response.status_code}")
    return response.json()


def parse_finnhub(payload: list[dict[str, Any]], group: str, since: datetime) -> list[Headline]:
    items = []
    for row in payload or []:
        if not row.get("headline") or not row.get("url") or not row.get("datetime"):
            continue
        published = datetime.fromtimestamp(int(row["datetime"]), UTC)
        if published < since:
            continue
        items.append(
            Headline(
                group=group,
                title=row["headline"].strip(),
                url=row["url"],
                source=row.get("source") or "",
                published=published,
                summary=(row.get("summary") or "").strip()[:300],
            )
        )
    items.sort(key=lambda h: h.published, reverse=True)
    return dedupe(items)


def parse_fed_rss(content: bytes, since: datetime) -> list[Headline]:
    root = ET.fromstring(content)
    items = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        raw_date = item.findtext("pubDate")
        if not title or not link or not raw_date:
            continue
        published = parsedate_to_datetime(raw_date)
        if published.tzinfo is None:
            published = published.replace(tzinfo=UTC)
        if published < since:
            continue
        summary = (item.findtext("description") or "").strip()[:300]
        items.append(Headline("fed", title, link, "Federal Reserve", published, summary))
    items.sort(key=lambda h: h.published, reverse=True)
    return items


def dedupe(items: list[Headline]) -> list[Headline]:
    seen, unique = set(), []
    for item in items:
        key = item.title.lower()
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique

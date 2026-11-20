"""NVIDIA's quarterly EPS versus consensus, from Finnhub."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import httpx

from bottom.store import RawStore

FINNHUB_URL = "https://finnhub.io/api/v1"


@dataclass(frozen=True)
class EarningsReport:
    date: date
    actual: float
    estimate: float
    period: str = ""
    approx_date: bool = False  # True when the report date was estimated from the quarter end

    @property
    def beat(self) -> bool:
        return self.actual > self.estimate


def fetch_reports(
    symbol: str, api_key: str, today: date, store: RawStore | None = None
) -> list[EarningsReport]:
    calendar = _get(
        "/calendar/earnings",
        {
            "symbol": symbol,
            "from": (today - timedelta(days=400)).isoformat(),
            "to": today.isoformat(),
        },
        api_key,
    )
    if store is not None:
        store.save_json(f"finnhub_calendar_{symbol}.json", calendar)
    reports = parse_calendar(calendar)
    if reports:
        return reports

    history = _get("/stock/earnings", {"symbol": symbol}, api_key)
    if store is not None:
        store.save_json(f"finnhub_earnings_{symbol}.json", history)
    return parse_history(history)


def _get(path: str, params: dict[str, str], api_key: str) -> Any:
    try:
        response = httpx.get(
            FINNHUB_URL + path,
            params=params,
            headers={"X-Finnhub-Token": api_key},
            timeout=30,
        )
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Finnhub {path}: {type(exc).__name__}") from None
    if response.status_code != 200:
        raise RuntimeError(f"Finnhub {path}: HTTP {response.status_code}")
    return response.json()


def parse_calendar(payload: dict[str, Any]) -> list[EarningsReport]:
    reports = []
    for row in payload.get("earningsCalendar") or []:
        actual, estimate = row.get("epsActual"), row.get("epsEstimate")
        if actual is None or estimate is None or not row.get("date"):
            continue  # upcoming reports have no actual yet
        period = f"Q{row['quarter']} FY{row['year']}" if row.get("quarter") else ""
        reports.append(
            EarningsReport(date.fromisoformat(row["date"]), float(actual), float(estimate), period)
        )
    return sorted(reports, key=lambda r: r.date)


def parse_history(payload: list[dict[str, Any]]) -> list[EarningsReport]:
    """/stock/earnings has no report date, only the quarter end; NVIDIA reports ~4 weeks later."""
    reports = []
    for row in payload or []:
        actual, estimate, period = row.get("actual"), row.get("estimate"), row.get("period")
        if actual is None or estimate is None or not period:
            continue
        reported = date.fromisoformat(period) + timedelta(days=28)
        reports.append(
            EarningsReport(reported, float(actual), float(estimate), period, approx_date=True)
        )
    return sorted(reports, key=lambda r: r.date)

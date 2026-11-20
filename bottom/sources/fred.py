"""FRED series: VIX, 2-year yield, fed funds target, high-yield spread."""

from __future__ import annotations

from datetime import date
from typing import Any

import httpx
import pandas as pd

from bottom.store import RawStore

FRED_URL = "https://api.stlouisfed.org/fred/series/observations"


def fetch_series(
    series_id: str, api_key: str, start: date, store: RawStore | None = None
) -> pd.Series:
    try:
        response = httpx.get(
            FRED_URL,
            params={
                "series_id": series_id,
                "api_key": api_key,
                "file_type": "json",
                "observation_start": start.isoformat(),
            },
            timeout=30,
        )
    except httpx.HTTPError as exc:
        # Do not include the request URL: it carries the API key.
        raise RuntimeError(f"FRED {series_id}: {type(exc).__name__}") from None
    if response.status_code != 200:
        raise RuntimeError(f"FRED {series_id}: HTTP {response.status_code}")

    payload = response.json()
    if store is not None:
        store.save_json(f"fred_{series_id}.json", payload)
    return parse_observations(payload)


def parse_observations(payload: dict[str, Any]) -> pd.Series:
    dates, values = [], []
    for obs in payload.get("observations", []):
        value = obs.get("value")
        if value in (None, "", "."):
            continue  # FRED marks holidays and missing days with "."
        dates.append(obs["date"])
        values.append(float(value))
    return pd.Series(values, index=pd.to_datetime(dates), dtype=float)

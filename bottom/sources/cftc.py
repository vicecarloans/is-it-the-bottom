"""CFTC Traders in Financial Futures: leveraged funds' positions in E-mini S&P 500 futures.

Weekly. Positions are measured each Tuesday and published the following Friday
through the CFTC's public reporting API. No key is needed.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import httpx
import pandas as pd

from bottom.store import RawStore

API = "https://publicreporting.cftc.gov/resource/gpe5-46if.json"  # TFF, futures only
EMINI_SP500 = "13874A"


def fetch_leverage(start: date, store: RawStore | None = None) -> pd.DataFrame:
    """Leveraged funds' long and short contracts, indexed by the Tuesday they were measured."""
    params = {
        "$select": "report_date_as_yyyy_mm_dd,lev_money_positions_long,lev_money_positions_short",
        "$where": (
            f"cftc_contract_market_code='{EMINI_SP500}' "
            f"AND report_date_as_yyyy_mm_dd >= '{start.isoformat()}'"
        ),
        "$order": "report_date_as_yyyy_mm_dd",
        "$limit": "5000",
    }
    response = httpx.get(API, params=params, timeout=60)
    response.raise_for_status()
    rows = response.json()
    if store is not None:
        store.save_json("cftc_tff_es.json", rows)
    return parse_tff(rows)


def parse_tff(rows: list[dict[str, Any]]) -> pd.DataFrame:
    records = []
    for row in rows:
        try:
            records.append(
                {
                    "date": pd.Timestamp(row["report_date_as_yyyy_mm_dd"][:10]),
                    "long": float(row["lev_money_positions_long"]),
                    "short": float(row["lev_money_positions_short"]),
                }
            )
        except (KeyError, TypeError, ValueError):
            continue
    if not records:
        raise RuntimeError("CFTC returned no E-mini S&P 500 rows")
    frame = pd.DataFrame(records).drop_duplicates("date").set_index("date").sort_index()
    return frame

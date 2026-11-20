"""FINRA margin statistics: monthly debit balances in customer margin accounts."""

from __future__ import annotations

import io
import re
from datetime import datetime
from urllib.parse import urljoin

import httpx
import pandas as pd

from bottom.sources import USER_AGENT
from bottom.store import RawStore

FINRA_PAGE = "https://www.finra.org/rules-guidance/key-topics/margin-accounts/margin-statistics"
FINRA_XLSX = "https://www.finra.org/sites/default/files/2021-03/margin-statistics.xlsx"


def fetch_margin(store: RawStore | None = None) -> pd.Series:
    """Debit balances in $ millions, indexed by the last day of each month."""
    content = _download(FINRA_XLSX)
    if content is None:
        content = _download(_find_xlsx_link())
    if content is None:
        raise RuntimeError("FINRA margin spreadsheet could not be downloaded")
    if store is not None:
        store.save_bytes("finra_margin.xlsx", content)
    return parse_margin_xlsx(content)


def _download(url: str | None) -> bytes | None:
    if not url:
        return None
    try:
        response = httpx.get(
            url, headers={"User-Agent": USER_AGENT}, timeout=60, follow_redirects=True
        )
    except httpx.HTTPError:
        return None
    if response.status_code != 200 or not response.content.startswith(b"PK"):
        return None  # .xlsx files are zip archives and start with "PK"
    return response.content


def _find_xlsx_link() -> str | None:
    """FINRA has moved the file before; look for it on the statistics page."""
    try:
        response = httpx.get(
            FINRA_PAGE, headers={"User-Agent": USER_AGENT}, timeout=30, follow_redirects=True
        )
    except httpx.HTTPError:
        return None
    match = re.search(r'href="([^"]*margin[^"]*\.xlsx)"', response.text, re.IGNORECASE)
    return urljoin(FINRA_PAGE, match.group(1)) if match else None


def parse_margin_xlsx(content: bytes) -> pd.Series:
    raw = pd.read_excel(io.BytesIO(content), header=None)

    header_row = None
    for i, row in raw.iterrows():
        if any(isinstance(v, str) and "month" in v.lower() for v in row.to_numpy()):
            header_row = i
            break
    if header_row is None:
        raise ValueError("FINRA spreadsheet: no Month/Year header found")

    header = ["" if pd.isna(v) else str(v).strip().lower() for v in raw.iloc[header_row]]
    month_col = next(i for i, h in enumerate(header) if "month" in h)
    debit_col = next((i for i, h in enumerate(header) if "debit" in h), None)
    if debit_col is None:
        raise ValueError("FINRA spreadsheet: no debit balances column found")

    body = raw.iloc[header_row + 1 :, [month_col, debit_col]].dropna()
    months = [_month_end(v) for v in body.iloc[:, 0]]
    values = pd.to_numeric(body.iloc[:, 1].astype(str).str.replace(",", ""), errors="coerce")

    series = pd.Series(values.to_numpy(), index=pd.DatetimeIndex(months), dtype=float)
    series = series[series.index.notna()].dropna()
    return series[~series.index.duplicated(keep="last")].sort_index()


def _month_end(value: object) -> pd.Timestamp:
    if isinstance(value, datetime | pd.Timestamp):
        stamp = pd.Timestamp(value)
    else:
        try:
            stamp = pd.to_datetime(str(value).strip(), format="%b-%y")
        except (ValueError, TypeError):
            return pd.NaT
    return (stamp + pd.offsets.MonthEnd(0)).normalize()

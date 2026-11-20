"""FINRA margin statistics: monthly debit balances in customer margin accounts."""

from __future__ import annotations

import io
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

import httpx
import pandas as pd

from bottom.sources import USER_AGENT
from bottom.store import RawStore

FINRA_PAGE = "https://www.finra.org/rules-guidance/key-topics/margin-accounts/margin-statistics"
FINRA_XLSX = "https://www.finra.org/sites/default/files/2021-03/margin-statistics.xlsx"

# A copy of FINRA's spreadsheet kept in the repo, used when the download is refused.
# FINRA publishes no data feed, so this file is updated by hand once a month.
LOCAL_COPY = Path("data/finra/margin-statistics.xlsx")


def fetch_margin(store: RawStore | None = None, local_copy: Path = LOCAL_COPY) -> pd.Series:
    """Debit balances in $ millions, indexed by the last day of each month."""
    reasons: list[str] = []
    with httpx.Client(
        headers={"User-Agent": USER_AGENT}, timeout=60, follow_redirects=True
    ) as client:
        content = _download(client, FINRA_XLSX, "download", reasons)
        if content is None:
            link = _find_xlsx_link(client, reasons)
            if link and link != FINRA_XLSX:
                content = _download(client, link, "linked download", reasons)
    if content is None and local_copy.exists():
        content = local_copy.read_bytes()
    if content is None:
        reasons.append(f"no copy at {local_copy}")
        raise RuntimeError(f"FINRA margin data unavailable ({'; '.join(reasons)})")
    if store is not None:
        store.save_bytes("finra_margin.xlsx", content)
    return parse_margin_xlsx(content)


def _download(client: httpx.Client, url: str, label: str, reasons: list[str]) -> bytes | None:
    try:
        response = client.get(url)
    except httpx.HTTPError as exc:
        reasons.append(f"{label}: {type(exc).__name__}")
        return None
    if response.status_code != 200:
        reasons.append(f"{label}: HTTP {response.status_code}")
        return None
    if not response.content.startswith(b"PK"):  # .xlsx files are zip archives
        reasons.append(f"{label}: not a spreadsheet ({response.headers.get('content-type')})")
        return None
    return response.content


def _find_xlsx_link(client: httpx.Client, reasons: list[str]) -> str | None:
    """FINRA has moved the file before; look for it on the statistics page."""
    try:
        response = client.get(FINRA_PAGE)
    except httpx.HTTPError as exc:
        reasons.append(f"page: {type(exc).__name__}")
        return None
    if response.status_code != 200:
        reasons.append(f"page: HTTP {response.status_code}")
        return None
    match = re.search(r'href="([^"]*margin[^"]*\.xlsx)"', response.text, re.IGNORECASE)
    if not match:
        reasons.append("page: no spreadsheet link found")
        return None
    return urljoin(FINRA_PAGE, match.group(1))


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
        # The web page shows "Oct-26"; the downloaded spreadsheet uses "2026-10".
        text = str(value).strip()
        for fmt in ("%Y-%m", "%b-%y", "%b-%Y", "%m/%Y"):
            try:
                stamp = pd.to_datetime(text, format=fmt)
                break
            except (ValueError, TypeError):
                continue
        else:
            return pd.NaT
    return (stamp + pd.offsets.MonthEnd(0)).normalize()

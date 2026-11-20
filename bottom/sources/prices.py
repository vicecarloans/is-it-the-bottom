"""Daily closing prices: yfinance first, Stooq as a fallback for any ticker it misses."""

from __future__ import annotations

import io
from datetime import date

import httpx
import pandas as pd

from bottom.sources import USER_AGENT

STOOQ_URL = "https://stooq.com/q/d/l/"


def fetch_closes(tickers: list[str], start: date) -> pd.DataFrame:
    """Return a DataFrame of daily closes, one column per ticker, indexed by date."""
    try:
        frame = _from_yfinance(tickers, start)
    except Exception:  # yfinance scrapes Yahoo and can fail in many ways
        frame = pd.DataFrame()

    columns: dict[str, pd.Series] = {}
    for ticker in tickers:
        if ticker in frame.columns and not frame[ticker].dropna().empty:
            columns[ticker] = frame[ticker].dropna()
            continue
        fallback = _from_stooq(ticker, start)
        if fallback is not None and not fallback.empty:
            columns[ticker] = fallback

    if not columns:
        raise RuntimeError("no prices from yfinance or Stooq")
    return pd.DataFrame(columns).sort_index()


def _from_yfinance(tickers: list[str], start: date) -> pd.DataFrame:
    import yfinance as yf

    data = yf.download(
        tickers,
        start=start.isoformat(),
        auto_adjust=True,
        progress=False,
        threads=False,
    )
    if data is None or data.empty:
        return pd.DataFrame()
    closes = data["Close"]
    if isinstance(closes, pd.Series):
        closes = closes.to_frame(tickers[0])
    closes.index = pd.DatetimeIndex(closes.index).tz_localize(None).normalize()
    return closes


def stooq_symbol(ticker: str) -> str | None:
    """Stooq names US listings like 'spy.us'. Indexes such as ^VIX are not covered."""
    if ticker.startswith("^"):
        return None
    return f"{ticker.lower()}.us"


def _from_stooq(ticker: str, start: date) -> pd.Series | None:
    symbol = stooq_symbol(ticker)
    if symbol is None:
        return None
    try:
        response = httpx.get(
            STOOQ_URL,
            params={"s": symbol, "i": "d"},
            headers={"User-Agent": USER_AGENT},
            timeout=30,
            follow_redirects=True,
        )
    except httpx.HTTPError:
        return None
    if response.status_code != 200:
        return None
    return parse_stooq_csv(response.text, start)


def parse_stooq_csv(text: str, start: date) -> pd.Series | None:
    try:
        frame = pd.read_csv(io.StringIO(text))
    except (pd.errors.ParserError, pd.errors.EmptyDataError):
        return None
    if "Date" not in frame.columns or "Close" not in frame.columns:
        return None
    series = pd.Series(
        pd.to_numeric(frame["Close"], errors="coerce").to_numpy(),
        index=pd.to_datetime(frame["Date"]),
    ).dropna()
    return series[series.index >= pd.Timestamp(start)]

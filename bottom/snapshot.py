"""Gathers every input for one run. A failing source is recorded, not fatal."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, TypeVar

import pandas as pd

from bottom.sources import earnings, finra, fred, prices
from bottom.sources.earnings import EarningsReport
from bottom.store import RawStore

T = TypeVar("T")


@dataclass
class Snapshot:
    closes: pd.DataFrame = field(default_factory=pd.DataFrame)
    vix: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    dgs2: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    fed_upper: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    hy_spread: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    margin: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    earnings: list[EarningsReport] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)


def load_snapshot(
    cfg: dict[str, Any], env: Mapping[str, str], today: date, store: RawStore
) -> Snapshot:
    snap = Snapshot()
    start = today - timedelta(days=int(cfg["history_days"]))
    tickers = cfg["tickers"]

    def attempt(source: str, fetch: Callable[[], T], default: T) -> T:
        try:
            return fetch()
        except Exception as exc:  # one broken source must not stop the run
            snap.errors[source] = str(exc) or type(exc).__name__
            return default

    symbols = [tickers["index"], *tickers["sectors"], tickers["vix"]]
    snap.closes = attempt("prices", lambda: prices.fetch_closes(symbols, start), pd.DataFrame())
    if not snap.closes.empty:
        store.save_frame("prices.csv", snap.closes)

    def from_fred(series_id: str) -> pd.Series:
        key = env.get("FRED_API_KEY")
        if not key:
            raise RuntimeError("FRED_API_KEY is not set")
        return fred.fetch_series(series_id, key, start, store)

    empty = pd.Series(dtype=float)
    vix_col = tickers["vix"]
    if vix_col in snap.closes.columns and not snap.closes[vix_col].dropna().empty:
        snap.vix = snap.closes[vix_col].dropna()
    else:
        snap.vix = attempt("fred VIXCLS", lambda: from_fred("VIXCLS"), empty)
    snap.dgs2 = attempt("fred DGS2", lambda: from_fred("DGS2"), empty)
    snap.fed_upper = attempt("fred DFEDTARU", lambda: from_fred("DFEDTARU"), empty)
    snap.hy_spread = attempt("fred BAMLH0A0HYM2", lambda: from_fred("BAMLH0A0HYM2"), empty)

    snap.margin = attempt("finra", lambda: finra.fetch_margin(store), empty)

    def nvda() -> list[EarningsReport]:
        key = env.get("FINNHUB_API_KEY")
        if not key:
            raise RuntimeError("FINNHUB_API_KEY is not set")
        return earnings.fetch_reports(cfg["nvda"]["symbol"], key, today, store)

    snap.earnings = attempt("earnings", nvda, [])
    return snap

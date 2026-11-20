"""The drawdown window and the five bottom conditions.

Every function takes the data and an `as_of` date and reads nothing newer than
that date, so the same code can later replay history for the backtest.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import pandas as pd

from bottom.sources.earnings import EarningsReport


@dataclass
class Drawdown:
    pct: float | None
    peak_date: date | None
    in_drawdown: bool
    data_date: date | None
    threshold_pct: float


@dataclass
class ConditionResult:
    key: str
    name: str
    met: bool | None  # None when the data was missing
    value: str
    threshold: str
    data_date: date | None = None
    detail: str = ""
    stale: bool = False

    @property
    def counts(self) -> bool:
        return bool(self.met) and not self.stale


def _upto(series: pd.Series, as_of: date) -> pd.Series:
    clean = series.dropna().sort_index()
    if clean.empty:
        return clean
    return clean[clean.index <= pd.Timestamp(as_of)]


def _missing(key: str, name: str, threshold: str, reason: str) -> ConditionResult:
    return ConditionResult(key, name, None, "no data", threshold, None, reason)


def drawdown(spy: pd.Series, as_of: date, cfg: dict[str, Any]) -> Drawdown:
    threshold = float(cfg["drawdown"]["threshold_pct"])
    series = _upto(spy, as_of)
    if len(series) < 2:
        return Drawdown(None, None, False, None, threshold)
    window = series.iloc[-int(cfg["drawdown"]["lookback_days"]) :]
    pct = round(float(series.iloc[-1] / window.max() - 1) * 100, 4)
    return Drawdown(
        pct=pct,
        peak_date=window.idxmax().date(),
        in_drawdown=pct <= threshold,
        data_date=series.index[-1].date(),
        threshold_pct=threshold,
    )


def vix_condition(vix: pd.Series, as_of: date, cfg: dict[str, Any]) -> ConditionResult:
    threshold = float(cfg["vix"]["threshold"])
    lookback = int(cfg["vix"]["lookback_days"])
    rule = f"closed above {threshold:g} in the last {lookback} trading days"
    name = "VIX above 30"
    series = _upto(vix, as_of)
    if series.empty:
        return _missing("vix", name, rule, "no VIX data")
    recent = series.iloc[-lookback:]
    peak = recent.max()
    return ConditionResult(
        key="vix",
        name=name,
        met=bool(peak > threshold),
        value=f"{series.iloc[-1]:.1f}",
        threshold=rule,
        data_date=series.index[-1].date(),
        detail=f"{lookback}-day peak {peak:.1f} on {recent.idxmax():%Y-%m-%d}",
    )


def fed_condition(
    dgs2: pd.Series, fed_upper: pd.Series, as_of: date, cfg: dict[str, Any]
) -> ConditionResult:
    max_spread = float(cfg["fed"]["max_spread_pts"])
    lookback = int(cfg["fed"]["hike_lookback_days"])
    rule = f"2y yield minus Fed upper target <= {max_spread:+.2f} pts, no hike in {lookback} days"
    name = "Fed not signaling hikes"
    yields, upper = _upto(dgs2, as_of), _upto(fed_upper, as_of)
    if yields.empty or upper.empty:
        return _missing("fed", name, rule, "no 2-year yield or Fed target data")

    spread = round(yields.iloc[-1] - upper.iloc[-1], 4)
    earlier = upper[upper.index <= pd.Timestamp(as_of - timedelta(days=lookback))]
    prior = earlier.iloc[-1] if not earlier.empty else upper.iloc[0]
    hiked = upper.iloc[-1] > prior + 1e-9

    detail = f"2y {yields.iloc[-1]:.2f}%, Fed upper target {upper.iloc[-1]:.2f}%"
    if hiked:
        detail += f", raised from {prior:.2f}% within {lookback} days"
    return ConditionResult(
        key="fed",
        name=name,
        met=bool(spread <= max_spread and not hiked),
        value=f"{spread:+.2f} pts",
        threshold=rule,
        data_date=min(yields.index[-1], upper.index[-1]).date(),
        detail=detail,
    )


def margin_condition(
    margin: pd.Series, as_of: date, cfg: dict[str, Any], apply_lag: bool = False
) -> ConditionResult:
    c = cfg["margin"]
    drop, months = float(c["drop_pct"]), int(c["window_months"])
    rule = f"{abs(drop):g}% or more below its {months}-month high"
    name = "Margin debt deleveraging"
    series = margin.dropna().sort_index()
    if series.empty:
        return _missing("margin", name, rule, "no FINRA margin data")
    cutoff = pd.Timestamp(as_of)
    if apply_lag:  # for past dates, only count months FINRA had published by then
        cutoff -= pd.Timedelta(days=int(c["publication_lag_days"]))
    series = series[series.index <= cutoff]
    if series.empty:
        return _missing("margin", name, rule, "no FINRA margin data")

    latest, month = series.iloc[-1], series.index[-1]
    change = round((latest / series.iloc[-months:].max() - 1) * 100, 4)
    stale = (as_of - month.date()).days > int(c["stale_after_days"])
    return ConditionResult(
        key="margin",
        name=name,
        met=bool(change <= drop and not stale),
        value=f"{change:+.1f}% vs high",
        threshold=rule,
        data_date=month.date(),
        detail=f"${latest / 1e6:.3f}T in {month:%B %Y} data"
        + (" (stale: counts as not met)" if stale else ""),
        stale=stale,
    )


def leader_stats(excess: pd.DataFrame) -> tuple[str, float, int]:
    """Given excess returns (rows = days, columns = sectors), return the latest leader,
    its excess over the index on the last day, and how many days it led."""
    leaders = excess.idxmax(axis=1)
    leader = leaders.iloc[-1]
    return leader, float(excess.iloc[-1][leader]), int((leaders == leader).sum())


def sector_condition(closes: pd.DataFrame, as_of: date, cfg: dict[str, Any]) -> ConditionResult:
    c, tickers = cfg["sector"], cfg["tickers"]
    window, min_excess = int(c["return_window_days"]), float(c["min_excess_pts"])
    persist, persist_min = int(c["persistence_window"]), int(c["persistence_min"])
    rule = (
        f"top sector beats SPY by {min_excess:g}+ pts over {window} days, "
        f"top on {persist_min} of the last {persist} days"
    )
    name = "A clear leading sector"
    index = tickers["index"]
    sectors = [s for s in tickers["sectors"] if s in closes.columns]
    if index not in closes.columns or not sectors:
        return _missing("sector", name, rule, "no sector price data")

    frame = closes[closes.index <= pd.Timestamp(as_of)][[index, *sectors]]
    returns = frame / frame.shift(window) - 1
    excess = returns[sectors].sub(returns[index], axis=0).mul(100).dropna(how="all")
    if len(excess) < persist:
        return _missing("sector", name, rule, "not enough price history")

    leader, lead_pts, days = leader_stats(excess.iloc[-persist:])
    return ConditionResult(
        key="sector",
        name=name,
        met=bool(round(lead_pts, 4) >= min_excess and days >= persist_min),
        value=f"{leader} {lead_pts:+.1f} pts vs {index}",
        threshold=rule,
        data_date=excess.index[-1].date(),
        detail=f"{leader} was the top sector on {days} of the last {persist} trading days",
    )


def nvda_condition(
    reports: list[EarningsReport], as_of: date, dd: Drawdown, cfg: dict[str, Any]
) -> ConditionResult:
    recent = int(cfg["nvda"]["recent_days"])
    rule = f"beat EPS consensus in a report from the drawdown or the last {recent} days"
    name = "NVIDIA beats earnings"
    past = [r for r in reports if r.date <= as_of]
    if not past:
        return _missing("nvda", name, rule, "no NVIDIA earnings data")

    latest = max(past, key=lambda r: r.date)
    window_start = as_of - timedelta(days=recent)
    if dd.in_drawdown and dd.peak_date is not None:
        window_start = min(window_start, dd.peak_date)
    in_window = latest.date >= window_start

    outcome = "beat" if latest.beat else "missed"
    detail = f"{outcome} in the report of {latest.date:%Y-%m-%d}"
    if latest.period:
        detail += f" ({latest.period})"
    if latest.approx_date:
        detail += ", report date estimated"
    if not in_window:
        detail += ", which is outside the window"
    return ConditionResult(
        key="nvda",
        name=name,
        met=bool(in_window and latest.beat),
        value=f"EPS {latest.actual:.2f} vs {latest.estimate:.2f} est",
        threshold=rule,
        data_date=latest.date,
        detail=detail,
    )

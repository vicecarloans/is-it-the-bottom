"""The systemic-break cap. The bottom rule excludes systemic breaks, so either check here
caps the verdict at "Getting closer"."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pandas as pd


@dataclass
class Override:
    capped: bool
    reasons: list[str] = field(default_factory=list)
    hy_level: float | None = None
    hy_change: float | None = None
    data_date: date | None = None


def check(
    hy_spread: pd.Series, as_of: date, cfg: dict[str, Any], digest: dict[str, Any] | None
) -> Override:
    window = int(cfg["override"]["hy_window_days"])
    limit = float(cfg["override"]["hy_widening_pts"])
    reasons: list[str] = []
    level = change = data_date = None

    series = hy_spread.dropna().sort_index()
    if not series.empty:
        series = series[series.index <= pd.Timestamp(as_of)]
    if len(series) >= 2:
        recent = series.iloc[-window:]
        level = round(float(series.iloc[-1]), 2)
        change = round(float(series.iloc[-1] - recent.min()), 2)
        data_date = series.index[-1].date()
        if change > limit:
            reasons.append(
                f"High-yield credit spreads widened {change:.1f} pts within {window} trading "
                f"days, to {level:.2f}%"
            )

    if digest and digest.get("systemic_flag"):
        reasons.append(f"News reports a systemic event: {digest['systemic_event']}")

    return Override(bool(reasons), reasons, level, change, data_date)

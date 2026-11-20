"""Writes the page's data: data/latest.json for today, data/history.json for every run."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from bottom import conditions as c
from bottom.snapshot import Snapshot
from bottom.verdict import Report

SPARK_DAYS = 126  # about six months of trading days
SPARK_MONTHS = 24
SPARK_REPORTS = 8
MARKET_DAYS = 520  # about two years for the drawdown chart


def _points(series: pd.Series, as_of: date, n: int) -> list[list[Any]]:
    clean = series.dropna().sort_index()
    if clean.empty:
        return []
    clean = clean[clean.index <= pd.Timestamp(as_of)].iloc[-n:]
    return [[d.strftime("%Y-%m-%d"), round(float(v), 4)] for d, v in clean.items()]


def sparklines(snap: Snapshot, as_of: date, cfg: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """A small series per condition, with the threshold it is judged against."""
    spark: dict[str, dict[str, Any]] = {
        "vix": {
            "label": "VIX close",
            "points": _points(snap.vix, as_of, SPARK_DAYS),
            "threshold": float(cfg["vix"]["threshold"]),
        }
    }

    spread = pd.Series(dtype=float)
    if not snap.dgs2.empty and not snap.fed_upper.empty:
        yields = snap.dgs2.dropna().sort_index()
        upper = snap.fed_upper.dropna().sort_index()
        upper = upper.reindex(upper.index.union(yields.index)).ffill().reindex(yields.index)
        spread = yields - upper
    spark["fed"] = {
        "label": "2-year yield minus Fed target, pts",
        "points": _points(spread, as_of, SPARK_DAYS),
        "threshold": float(cfg["fed"]["max_spread_pts"]),
    }

    margin_pct = pd.Series(dtype=float)
    if not snap.margin.empty:
        monthly = snap.margin.dropna().sort_index()
        window = int(cfg["margin"]["window_months"])
        margin_pct = (monthly / monthly.rolling(window, min_periods=1).max() - 1) * 100
    spark["margin"] = {
        "label": "Margin debt vs its 12-month high, %",
        "points": _points(margin_pct, as_of, SPARK_MONTHS),
        "threshold": float(cfg["margin"]["drop_pct"]),
    }

    leader_series = pd.Series(dtype=float)
    leader = None
    excess = c.sector_excess(snap.closes, as_of, cfg)
    if excess is not None and not excess.empty:
        leader = excess.iloc[-1].idxmax()
        leader_series = excess[leader]
    spark["sector"] = {
        "label": f"{leader} return minus SPY over 20 days, pts" if leader else "Sector lead",
        "points": _points(leader_series, as_of, SPARK_DAYS),
        "threshold": float(cfg["sector"]["min_excess_pts"]),
    }

    surprises = [
        [r.date.isoformat(), round((r.actual - r.estimate) / abs(r.estimate) * 100, 2)]
        for r in snap.earnings
        if r.date <= as_of and r.estimate
    ][-SPARK_REPORTS:]
    spark["nvda"] = {"label": "NVIDIA EPS surprise, %", "points": surprises, "threshold": 0.0}
    return spark


def market_series(snap: Snapshot, as_of: date, cfg: dict[str, Any]) -> list[list[Any]]:
    """SPY close and its drawdown from the rolling one-year high, for the chart."""
    index = cfg["tickers"]["index"]
    if index not in snap.closes.columns:
        return []
    spy = snap.closes[index].dropna().sort_index()
    spy = spy[spy.index <= pd.Timestamp(as_of)]
    lookback = int(cfg["drawdown"]["lookback_days"])
    drawdown = (spy / spy.rolling(lookback, min_periods=1).max() - 1) * 100
    rows = pd.DataFrame({"close": spy, "dd": drawdown}).iloc[-MARKET_DAYS:]
    return [
        [d.strftime("%Y-%m-%d"), round(float(r.close), 2), round(float(r.dd), 2)]
        for d, r in rows.iterrows()
    ]


def _iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, date) else value


def build_latest(
    report: Report, snap: Snapshot, cfg: dict[str, Any], now: datetime | None = None
) -> dict[str, Any]:
    spark = sparklines(snap, report.as_of, cfg)
    conditions = []
    for result in report.results:
        item = {k: _iso(v) for k, v in asdict(result).items()}
        item["spark"] = spark.get(result.key)
        conditions.append(item)
    dd = report.drawdown
    return {
        "as_of": report.as_of.isoformat(),
        "generated_at": (now or datetime.now(UTC)).isoformat(timespec="seconds"),
        "verdict": report.verdict,
        "count": report.count,
        "total": len(report.results),
        "backtested": False,
        "drawdown": {
            "pct": dd.pct,
            "peak_date": _iso(dd.peak_date),
            "in_drawdown": dd.in_drawdown,
            "threshold_pct": dd.threshold_pct,
            "data_date": _iso(dd.data_date),
        },
        "conditions": conditions,
        "market": market_series(snap, report.as_of, cfg),
        "errors": report.errors,
    }


def update_history(history: dict[str, Any] | None, latest: dict[str, Any]) -> dict[str, Any]:
    """Add today's run, replacing any earlier run for the same date, and recompute signals."""
    day = {
        "date": latest["as_of"],
        "verdict": latest["verdict"],
        "count": latest["count"],
        "in_drawdown": latest["drawdown"]["in_drawdown"],
        "drawdown_pct": latest["drawdown"]["pct"],
        "met": {
            cond["key"]: None if cond["met"] is None else cond["met"] and not cond["stale"]
            for cond in latest["conditions"]
        },
    }
    days = [d for d in (history or {}).get("days", []) if d["date"] != day["date"]]
    days.append(day)
    days.sort(key=lambda d: d["date"])
    return {"days": days, "signals": signals(days)}


def signals(days: list[dict[str, Any]]) -> list[dict[str, str]]:
    """The first day of each run of days on which all five conditions held."""
    found, previous = [], False
    for day in days:
        current = bool(day["in_drawdown"]) and day["count"] >= 5
        if current and not previous:
            found.append({"date": day["date"]})
        previous = current
    return found


def write_outputs(out_dir: Path, latest: dict[str, Any]) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    history_path = out_dir / "history.json"
    history = json.loads(history_path.read_text()) if history_path.exists() else None
    history = update_history(history, latest)
    (out_dir / "latest.json").write_text(_dump(latest))
    history_path.write_text(_dump(history))
    return history


def _dump(payload: dict[str, Any]) -> str:
    # allow_nan=False makes a bad number fail loudly instead of breaking the page.
    return json.dumps(payload, indent=1, allow_nan=False) + "\n"

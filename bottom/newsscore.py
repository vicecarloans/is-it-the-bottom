"""Turns tagged headlines into a news score for the market and each sector line, and checks
whether prices moved the way the news points.

Each story counts once, with the tags of its first headline. Its weight is
direction x strength x confidence x novelty, halving every few trading days. Its
price reaction compares the line's move with the S&P 500 from the last close before the
story to the first close after it:
- confirmed: moved the way the news points by more than the threshold
- faded: moved the other way by more than the threshold (good news, falling price)
- muted: moved less than the threshold either way
- pending: no close since the story yet
Scores and reactions are context for the page; they never set a condition.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

NEW_YORK = ZoneInfo("America/New_York")
SIGN = {"positive": 1, "negative": -1, "neutral": 0}
CONFIDENCE_WEIGHT = {"low": 0.5, "medium": 0.75, "high": 1.0}
NOVELTY_WEIGHT = {"new": 1.0, "follow_up": 0.5, "rehash": 0.0}


def load_stories(
    news_dir: Path, today: list[dict[str, Any]] | None, as_of: date, lookback_days: int
) -> list[dict[str, Any]]:
    """One record per story from the saved files of recent days plus today's headlines.

    A headline seen on several days keeps its first tags. Within a day, headlines that
    share a story id are one story, represented by the earliest.
    """
    days: list[tuple[str, list[dict[str, Any]]]] = []
    start = pd.Timestamp(as_of) - pd.Timedelta(days=lookback_days)
    for path in sorted(news_dir.glob("*.json")) if news_dir.exists() else []:
        day = path.stem
        if day >= as_of.isoformat() or pd.Timestamp(day) < start:
            continue  # today's file is replaced by today's headlines below
        try:
            days.append((day, json.loads(path.read_text())["headlines"]))
        except (ValueError, KeyError):
            continue
    if today:
        days.append((as_of.isoformat(), today))

    seen_urls: set[str] = set()
    stories: list[dict[str, Any]] = []
    for day, headlines in days:
        firsts: dict[int, dict[str, Any]] = {}
        for h in sorted(headlines, key=lambda h: h.get("published") or ""):
            url = h.get("url")
            if not url or url in seen_urls or not h.get("tags"):
                continue
            seen_urls.add(url)
            key = h.get("story", h.get("id"))
            if key in firsts:
                firsts[key]["count"] += 1
                continue
            firsts[key] = {**h, "day": day, "count": 1}
        stories += firsts.values()
    return stories


def published_at(story: dict[str, Any]) -> pd.Timestamp:
    return pd.Timestamp(datetime.fromisoformat(story["published"])).tz_convert(NEW_YORK)


def reaction_window(
    published: pd.Timestamp, trading_days: pd.DatetimeIndex
) -> tuple[pd.Timestamp | None, pd.Timestamp | None]:
    """The last close before the story and the first close after it (closes at 4:00 pm)."""
    closes = [d + pd.Timedelta(hours=16) for d in trading_days]
    naive = published.tz_localize(None)
    before = [d for d, c in zip(trading_days, closes, strict=True) if c <= naive]
    after = [d for d, c in zip(trading_days, closes, strict=True) if c > naive]
    return (before[-1] if before else None), (after[0] if after else None)


def move(closes: pd.DataFrame, ticker: str, start: pd.Timestamp, end: pd.Timestamp) -> float | None:
    if ticker not in closes.columns:
        return None
    a, b = closes.at[start, ticker], closes.at[end, ticker]
    if pd.isna(a) or pd.isna(b) or not a:
        return None
    return float((b / a - 1) * 100)


def label(direction: int, change: float | None, threshold: float) -> str:
    if change is None:
        return "pending"
    if abs(change) < threshold:
        return "muted"
    return "confirmed" if (change > 0) == (direction > 0) else "faded"


def score(
    stories: list[dict[str, Any]],
    closes: pd.DataFrame,
    as_of: date,
    cfg: dict[str, Any],
) -> dict[str, Any]:
    n, s = cfg["news"], cfg["news"]["scoring"]
    index = cfg["tickers"]["index"]
    lines: dict[str, str] = n["lines"]
    half_life = float(s["half_life_trading_days"])
    threshold = float(s["reaction_threshold_pct"])

    frame = closes[closes.index <= pd.Timestamp(as_of)] if not closes.empty else closes
    trading_days = frame.index if not frame.empty else pd.DatetimeIndex([])
    today = pd.Timestamp(as_of)

    totals = {code: {"score": 0.0, "stories": []} for code in lines}
    macro = {"score": 0.0, "stories": [], "channels": {}}
    for story in stories:
        tags = story["tags"]
        weight = CONFIDENCE_WEIGHT[tags["confidence"]] * NOVELTY_WEIGHT[tags["novelty"]]
        if not weight:
            continue
        published = published_at(story)
        # Age in trading days, counted from the first close after the story.
        close_times = trading_days + pd.Timedelta(hours=16)
        after = int(((close_times > published.tz_localize(None)) & (trading_days <= today)).sum())
        age = max(after - 1, 0)
        decay = 0.5 ** (age / half_life)
        start, end = reaction_window(published, trading_days)
        base = {
            "title": story["title"],
            "url": story["url"],
            "source": story["source"],
            "published": story["published"],
            "headlines": story["count"],
        }

        direction = SIGN[tags["macro"]]
        if direction:
            change = (
                move(frame, index, start, end) if start is not None and end is not None else None
            )
            points = direction * weight * decay
            macro["score"] += points
            channel = tags["channel"]
            macro["channels"][channel] = macro["channels"].get(channel, 0.0) + points
            macro["stories"].append(
                {**base, "direction": tags["macro"], "channel": channel, "points": points,
                 "reaction": label(direction, change, threshold), "change": change}
            )  # fmt: skip

        for tag in tags["sectors"]:
            code = tag["line"]
            if code not in totals:
                continue
            direction = SIGN[tag["direction"]]
            change = None
            if start is not None and end is not None:
                line_move, index_move = (
                    move(frame, code, start, end),
                    move(frame, index, start, end),
                )
                if line_move is not None and index_move is not None:
                    change = line_move - index_move
            points = direction * tag["strength"] * weight * decay
            totals[code]["score"] += points
            reaction = label(direction, change, threshold)
            totals[code]["stories"].append(
                {**base, "direction": tag["direction"], "strength": tag["strength"],
                 "points": points, "reaction": reaction, "change": change}
            )  # fmt: skip

    def summarize(name: str, total: dict[str, Any]) -> dict[str, Any]:
        ranked = sorted(total["stories"], key=lambda x: abs(x["points"]), reverse=True)
        counts = {k: 0 for k in ("confirmed", "faded", "muted", "pending")}
        for item in total["stories"]:
            counts[item["reaction"]] += 1
        return {
            "name": name,
            "score": round(total["score"], 2),
            "tone": tone(total["score"], s),
            "stories": len(total["stories"]),
            "reactions": counts,
            "top": [_round(x) for x in ranked[:3]],
        }

    out_macro = summarize("Market overall", macro)
    out_macro["channels"] = {
        k: round(v, 2) for k, v in sorted(macro["channels"].items(), key=lambda kv: -abs(kv[1]))
    }
    return {
        "as_of": as_of.isoformat(),
        "macro": out_macro,
        "lines": {code: summarize(name, totals[code]) for code, name in lines.items()},
    }


def tone(value: float, s: dict[str, Any]) -> str:
    strong, mild = float(s["strong_at"]), float(s["mild_at"])
    if value >= strong:
        return "strongly positive"
    if value >= mild:
        return "positive"
    if value <= -strong:
        return "strongly negative"
    if value <= -mild:
        return "negative"
    return "mixed or quiet"


def _round(item: dict[str, Any]) -> dict[str, Any]:
    out = dict(item)
    out["points"] = round(out["points"], 2)
    if out.get("change") is not None:
        out["change"] = round(out["change"], 2)
    return out

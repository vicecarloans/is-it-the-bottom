"""Turns the drawdown and the five conditions into one verdict."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pandas as pd

from bottom import conditions as c
from bottom.override import Override
from bottom.snapshot import Snapshot

NOT_IN_DRAWDOWN = "No panic, no bottom to call"
NO = "No"
CLOSER = "Getting closer"
BOTTOM = "Looks like a bottom"


@dataclass
class Report:
    as_of: date
    drawdown: c.Drawdown
    results: list[c.ConditionResult]
    errors: dict[str, str] = field(default_factory=dict)
    override: Override | None = None
    catalyst: dict[str, Any] | None = None
    digest: dict[str, Any] | None = None

    @property
    def count(self) -> int:
        return sum(r.counts for r in self.results)

    @property
    def base_verdict(self) -> str:
        """The verdict from the five conditions alone."""
        return level(self.drawdown.in_drawdown, self.count)

    @property
    def catalyst_lift(self) -> bool:
        return bool(self.catalyst and self.catalyst.get("active") and self.drawdown.in_drawdown)

    @property
    def capped(self) -> bool:
        return bool(self.override and self.override.capped)

    @property
    def verdict(self) -> str:
        """The base verdict, lifted one level by the catalyst, then capped by a systemic break."""
        result = self.base_verdict
        if self.catalyst_lift:
            result = raise_one(result)
        if self.capped and result == BOTTOM:
            result = CLOSER
        return result


def level(in_drawdown: bool, count: int) -> str:
    if not in_drawdown:
        return NOT_IN_DRAWDOWN
    if count >= 5:
        return BOTTOM
    if count >= 3:
        return CLOSER
    return NO


def raise_one(verdict: str) -> str:
    return {NO: CLOSER, CLOSER: BOTTOM}.get(verdict, verdict)


def evaluate(snap: Snapshot, as_of: date, cfg: dict[str, Any], apply_lag: bool) -> Report:
    index = cfg["tickers"]["index"]
    spy = snap.closes[index] if index in snap.closes.columns else pd.Series(dtype=float)
    dd = c.drawdown(spy, as_of, cfg)
    results = [
        c.vix_condition(snap.vix, as_of, cfg),
        c.fed_condition(snap.dgs2, snap.fed_upper, as_of, cfg),
        c.margin_condition(snap.margin, as_of, cfg, apply_lag=apply_lag),
        c.sector_condition(snap.closes, as_of, cfg),
        c.nvda_condition(snap.earnings, as_of, dd, cfg),
    ]
    return Report(as_of, dd, results, dict(snap.errors))

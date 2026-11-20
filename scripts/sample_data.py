"""Writes made-up but realistic data to site/sample/ for previewing the page locally.

uv run python scripts/sample_data.py
python -m http.server -d site 8000   # then open http://localhost:8000/?data=sample/
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bottom.config import load_config  # noqa: E402
from bottom.output import build_latest, write_outputs  # noqa: E402
from bottom.snapshot import Snapshot  # noqa: E402
from bottom.sources.earnings import EarningsReport  # noqa: E402
from bottom.sources.finra import parse_margin_xlsx  # noqa: E402
from bottom.verdict import evaluate  # noqa: E402

AS_OF = date(2026, 11, 20)


def main() -> None:
    cfg = load_config(ROOT / "config.yaml")
    rng = np.random.default_rng(7)
    days = pd.bdate_range(end=AS_OF, periods=560)
    n = len(days)

    drift = np.full(n, 0.0005)
    drift[-32:] = -0.0030  # a recent selloff
    spy = 480 * np.exp(np.cumsum(drift + rng.normal(0, 0.008, n)))
    closes = pd.DataFrame({"SPY": spy}, index=days)
    for sector in cfg["tickers"]["sectors"]:
        tilt = 0.0009 if sector == "XLP" else 0.0
        noise = rng.normal(0, 0.009, n)
        closes[sector] = 100 * np.exp(np.cumsum(drift + noise + tilt * (np.arange(n) > n - 20)))

    vix = pd.Series(16 + np.abs(rng.normal(0, 2, n)), index=days)
    vix.iloc[-8:] = [27, 31, 34, 33, 36, 35.9, 33, 35.9]

    calendar = pd.date_range(end=AS_OF, periods=760)
    margin_path = ROOT / "data/finra/margin-statistics.xlsx"
    margin = parse_margin_xlsx(margin_path.read_bytes()) if margin_path.exists() else pd.Series()

    snap = Snapshot(
        closes=closes,
        vix=vix,
        dgs2=pd.Series(np.linspace(3.9, 4.76, len(calendar)), index=calendar),
        fed_upper=pd.Series(4.0, index=calendar),
        margin=margin,
        earnings=[
            EarningsReport(AS_OF - timedelta(days=d), a, e, p)
            for d, a, e, p in [
                (359, 1.68, 1.61, "Q3 FY2026"),
                (268, 1.89, 1.84, "Q4 FY2026"),
                (177, 2.10, 2.12, "Q1 FY2027"),
                (86, 2.41, 2.30, "Q2 FY2027"),
                (3, 2.68, 2.52, "Q3 FY2027"),
            ]
        ],
    )
    report = evaluate(snap, AS_OF, cfg, apply_lag=False)
    out = ROOT / "site/sample"
    for name in ("latest.json", "history.json"):
        (out / name).unlink(missing_ok=True)
    write_outputs(out, build_latest(report, snap, cfg))
    print(f"Wrote sample data to {out}: {report.verdict}, {report.count} of 5")


if __name__ == "__main__":
    main()

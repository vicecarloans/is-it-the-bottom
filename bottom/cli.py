"""Command line: `bottom check` prints today's checklist in the terminal."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path

from bottom.config import load_config
from bottom.snapshot import load_snapshot
from bottom.store import RawStore
from bottom.verdict import Report, evaluate


def render(report: Report) -> str:
    dd = report.drawdown
    lines = [f"Is it the bottom?  as of {report.as_of:%Y-%m-%d}", ""]

    if dd.pct is None:
        lines.append("S&P 500 drawdown: no price data")
    else:
        state = "in a drawdown" if dd.in_drawdown else "not in a drawdown"
        lines.append(
            f"S&P 500 drawdown: {dd.pct:.1f}% from the {dd.peak_date:%Y-%m-%d} high "
            f"(window opens at {dd.threshold_pct:g}%), {state}"
        )
    lines.append("")

    rows = [("#", "Condition", "Value", "Met", "Data date")]
    for i, r in enumerate(report.results, start=1):
        met = "n/a" if r.met is None else ("yes" if r.met else "no")
        if r.stale:
            met += " (stale)"
        rows.append((str(i), r.name, r.value, met, r.data_date.isoformat() if r.data_date else "-"))
    widths = [max(len(row[col]) for row in rows) for col in range(len(rows[0]))]
    for n, row in enumerate(rows):
        lines.append("  ".join(cell.ljust(w) for cell, w in zip(row, widths, strict=True)).rstrip())
        if n == 0:
            lines.append("  ".join("-" * w for w in widths))

    lines += ["", "Details:"]
    for i, r in enumerate(report.results, start=1):
        lines.append(f"  {i}. Rule: {r.threshold}")
        if r.detail:
            lines.append(f"     {r.detail}")

    if report.errors:
        lines += ["", "Data problems:"]
        lines += [f"  - {source}: {message}" for source, message in report.errors.items()]

    lines += [
        "",
        f"Conditions met: {report.count} of {len(report.results)}",
        f"Verdict: {report.verdict}",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bottom", description="Is it the bottom?")
    commands = parser.add_subparsers(dest="command", required=True)

    check = commands.add_parser("check", help="print today's checklist in the terminal")
    check.add_argument("--config", default="config.yaml", help="path to config.yaml")
    check.add_argument(
        "--as-of", type=date.fromisoformat, help="evaluate as of this date (YYYY-MM-DD)"
    )
    check.add_argument("--no-save", action="store_true", help="do not save raw responses")

    args = parser.parse_args(argv)
    cfg = load_config(args.config)
    today = date.today()
    as_of = args.as_of or today

    store = RawStore(Path("data/raw"), today, enabled=not args.no_save)
    snapshot = load_snapshot(cfg, os.environ, today, store)
    report = evaluate(snapshot, as_of, cfg, apply_lag=as_of < today)
    print(render(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())

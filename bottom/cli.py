"""Command line: `bottom check` prints today's checklist in the terminal."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path

from bottom import override
from bottom.config import load_config
from bottom.extras import enrich
from bottom.output import build_latest, write_cftc, write_outputs
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

    if report.digest:
        lines += ["", "News digest:", f"  {report.digest['summary']}"]
        if report.digest.get("huang_return_reported"):
            lines.append(f"  Watch: {report.digest['huang_note']}")

    lines += ["", f"Conditions met: {report.count} of {len(report.results)}"]
    if report.catalyst_lift:
        event = report.catalyst["event"]
        lines.append(f"Catalyst: Jensen Huang named NVIDIA CEO ({event['date']}), +1 level")
    if report.capped:
        lines += [f"Cap: {reason}" for reason in report.override.reasons]
    lines.append(f"Verdict: {report.verdict}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bottom", description="Is it the bottom?")
    commands = parser.add_subparsers(dest="command", required=True)

    check = commands.add_parser("check", help="print today's checklist in the terminal")
    run = commands.add_parser("run", help="write latest.json and update history.json")
    run.add_argument("--out", default="data", help="folder for latest.json and history.json")
    for sub in (check, run):
        sub.add_argument("--config", default="config.yaml", help="path to config.yaml")
        sub.add_argument("--no-save", action="store_true", help="do not save raw responses")
    check.add_argument(
        "--as-of", type=date.fromisoformat, help="evaluate as of this date (YYYY-MM-DD)"
    )

    args = parser.parse_args(argv)
    cfg = load_config(args.config)
    today = date.today()
    as_of = getattr(args, "as_of", None) or today

    store = RawStore(Path("data/raw"), today, enabled=not args.no_save)
    snapshot = load_snapshot(cfg, os.environ, today, store)
    report = evaluate(snapshot, as_of, cfg, apply_lag=as_of < today)
    if args.command == "run":
        enrich(report, snapshot, cfg, os.environ, today, store, Path(args.out) / "catalyst.json")
    else:  # the terminal check skips the news and filings, which need the model
        report.override = override.check(snapshot.hy_spread, as_of, cfg, None)
    print(render(report))

    if args.command == "run":
        latest = build_latest(report, snapshot, cfg)
        history = write_outputs(Path(args.out), latest)
        write_cftc(Path(args.out), snapshot.leverage)
        print(
            f"\nWrote {args.out}/latest.json and {args.out}/history.json "
            f"({len(history['days'])} days, {len(history['signals'])} signals)"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())

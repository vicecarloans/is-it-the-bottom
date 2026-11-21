from datetime import UTC, date, timedelta

import numpy as np
import pandas as pd

from bottom import cli, verdict
from bottom.snapshot import Snapshot
from bottom.sources.earnings import EarningsReport

from .conftest import ROOT

AS_OF = date(2026, 11, 20)


def panic_snapshot(cfg) -> Snapshot:
    """A market 15% off its high in which all five conditions hold."""
    index = pd.bdate_range(end=AS_OF, periods=300)
    closes = pd.DataFrame(100.0, index=index, columns=["SPY", *cfg["tickers"]["sectors"]])
    closes.iloc[-30:, 0] = 85.0
    closes["XLE"] = 100.0 * 1.003 ** np.arange(300)
    days = pd.date_range(end=AS_OF, periods=300)
    return Snapshot(
        closes=closes,
        vix=pd.Series([20.0] * 295 + [38.0, 35.0, 33.0, 31.0, 29.0], index=index[-300:]),
        dgs2=pd.Series(3.9, index=days),
        fed_upper=pd.Series(4.25, index=days),
        margin=pd.Series(
            [100.0] * 11 + [85.0], index=pd.date_range(end="2026-10-31", periods=12, freq="ME")
        ),
        earnings=[EarningsReport(AS_OF - timedelta(days=5), 1.2, 1.0, "Q3 FY2027")],
    )


def test_all_five_met_is_a_bottom(cfg):
    report = verdict.evaluate(panic_snapshot(cfg), AS_OF, cfg, apply_lag=False)
    assert report.drawdown.in_drawdown
    assert [r.met for r in report.results] == [True] * 5
    assert report.verdict == verdict.BOTTOM


def test_levels():
    assert verdict.level(False, 5) == verdict.NOT_IN_DRAWDOWN
    assert verdict.level(True, 2) == verdict.NO
    assert verdict.level(True, 3) == verdict.CLOSER
    assert verdict.level(True, 4) == verdict.CLOSER
    assert verdict.level(True, 5) == verdict.BOTTOM


def test_render_lists_every_condition(cfg):
    snap = panic_snapshot(cfg)
    snap.errors["finra"] = "FINRA margin spreadsheet could not be downloaded"
    text = cli.render(verdict.evaluate(snap, AS_OF, cfg, apply_lag=False))
    for name in [
        "VIX above 30",
        "Fed not signaling hikes",
        "Margin debt deleveraging",
        "A clear leading sector",
        "NVIDIA beats earnings",
    ]:
        assert name in text
    assert "Conditions met: 5 of 5" in text
    assert "Verdict: Looks like a bottom" in text
    assert "Data problems:" in text


def test_empty_snapshot_still_renders(cfg):
    report = verdict.evaluate(Snapshot(), AS_OF, cfg, apply_lag=False)
    text = cli.render(report)
    assert "no price data" in text
    assert report.count == 0


def test_main_runs_check(cfg, monkeypatch, capsys):
    monkeypatch.setattr(cli, "load_snapshot", lambda *args: panic_snapshot(cfg))
    code = cli.main(
        ["check", "--config", str(ROOT / "config.yaml"), "--as-of", "2026-11-20", "--no-save"]
    )
    assert code == 0
    assert "Verdict:" in capsys.readouterr().out


def test_evening_utc_run_is_dated_by_new_york():
    from datetime import datetime

    # 01:56 UTC on Saturday Nov 21 is 8:56 pm Friday in New York.
    assert cli.market_today(datetime(2026, 11, 21, 1, 56, tzinfo=UTC)) == date(
        2026, 11, 20
    )


def test_weekend_run_belongs_to_friday():
    from datetime import datetime

    assert cli.market_today(datetime(2026, 11, 22, 18, 0, tzinfo=UTC)) == date(
        2026, 11, 20
    )


def test_run_with_older_prices_does_not_replace_saved_result(tmp_path):
    saved = tmp_path / "latest.json"
    saved.write_text('{"drawdown": {"data_date": "2026-11-20"}}')
    assert cli.went_backwards(saved, {"drawdown": {"data_date": "2026-11-19"}})
    assert cli.went_backwards(saved, {"drawdown": {"data_date": "2026-11-20"}}) is None
    assert (
        cli.went_backwards(tmp_path / "missing.json", {"drawdown": {"data_date": "2026-11-19"}})
        is None
    )

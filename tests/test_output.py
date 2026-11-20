import json
from datetime import UTC, date, datetime

from bottom import cli, output, verdict

from .conftest import ROOT
from .test_cli import AS_OF, panic_snapshot

NOW = datetime(2026, 11, 20, 22, 30, tzinfo=UTC)


def latest_for(cfg, snap=None):
    snap = snap or panic_snapshot(cfg)
    report = verdict.evaluate(snap, AS_OF, cfg, apply_lag=False)
    return output.build_latest(report, snap, cfg, now=NOW)


def test_latest_has_everything_the_page_reads(cfg):
    latest = latest_for(cfg)
    assert latest["as_of"] == "2026-11-20"
    assert latest["verdict"] == verdict.BOTTOM
    assert (latest["count"], latest["total"]) == (5, 5)
    assert latest["backtested"] is False
    assert latest["drawdown"]["in_drawdown"] is True
    keys = [c["key"] for c in latest["conditions"]]
    assert keys == ["vix", "fed", "margin", "sector", "nvda"]
    for cond in latest["conditions"]:
        assert cond["spark"]["points"], cond["key"]
        assert isinstance(cond["data_date"], str)
    assert latest["market"][-1][0] == "2026-11-20"
    assert latest["market"][-1][2] == -15.0
    json.dumps(latest, allow_nan=False)  # must serialise cleanly


def test_sparkline_thresholds_follow_config(cfg):
    spark = {c["key"]: c["spark"] for c in latest_for(cfg)["conditions"]}
    assert spark["vix"]["threshold"] == 30
    assert spark["margin"]["threshold"] == -10
    assert spark["margin"]["also"][0]["threshold"] == -15
    assert spark["nvda"]["points"] == [["2026-11-15", 20.0]]
    assert spark["sector"]["label"].startswith("XLE")


def day(d, count, in_dd=True):
    return {"date": d, "count": count, "in_drawdown": in_dd}


def test_signals_mark_the_first_day_of_each_run():
    days = [
        day("2026-03-02", 4),
        day("2026-03-03", 5),
        day("2026-03-04", 5),
        day("2026-03-05", 3),
        day("2026-03-06", 5),
        day("2026-03-09", 5, in_dd=False),
    ]
    assert output.signals(days) == [{"date": "2026-03-03"}, {"date": "2026-03-06"}]


def test_history_replaces_a_rerun_of_the_same_day(cfg):
    latest = latest_for(cfg)
    history = output.update_history(None, latest)
    history = output.update_history(history, latest)
    assert len(history["days"]) == 1
    assert history["signals"] == [{"date": "2026-11-20"}]
    assert history["days"][0]["met"] == {
        k: True for k in ["vix", "fed", "margin", "sector", "nvda"]
    }


def test_run_writes_both_files(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "load_snapshot", lambda *args: panic_snapshot(cfg))
    monkeypatch.setattr(cli, "enrich", lambda report, *args: report)  # no network in tests
    monkeypatch.setattr(
        cli,
        "date",
        type("D", (), {"today": staticmethod(lambda: AS_OF), "fromisoformat": date.fromisoformat}),
    )
    code = cli.main(
        ["run", "--config", str(ROOT / "config.yaml"), "--out", str(tmp_path), "--no-save"]
    )
    assert code == 0
    latest = json.loads((tmp_path / "latest.json").read_text())
    history = json.loads((tmp_path / "history.json").read_text())
    assert latest["as_of"] == "2026-11-20"
    assert [d["date"] for d in history["days"]] == ["2026-11-20"]


def test_cftc_history_is_saved(tmp_path):
    import pandas as pd

    from bottom.output import write_cftc

    lev = pd.DataFrame(
        {"long": [1.0, 2.0], "short": [3.0, 4.0]},
        index=pd.to_datetime(["2026-11-03", "2026-11-10"]),
    )
    write_cftc(tmp_path, lev)
    saved = json.loads((tmp_path / "cftc.json").read_text())
    assert saved["rows"][-1] == ["2026-11-10", 2, 4]


def test_every_run_is_archived_by_date(tmp_path):
    latest = {
        "as_of": "2026-11-20",
        "verdict": "No",
        "count": 2,
        "drawdown": {"in_drawdown": True, "pct": -11.5},
        "conditions": [],
    }
    output.write_outputs(tmp_path, latest)
    output.write_outputs(tmp_path, {**latest, "as_of": "2026-11-23", "count": 3})
    assert json.loads((tmp_path / "days" / "2026-11-20.json").read_text())["count"] == 2
    assert json.loads((tmp_path / "days" / "2026-11-23.json").read_text())["count"] == 3
    days = json.loads((tmp_path / "history.json").read_text())["days"]
    assert [d["date"] for d in days] == ["2026-11-20", "2026-11-23"]
    assert all(d["archived"] for d in days)

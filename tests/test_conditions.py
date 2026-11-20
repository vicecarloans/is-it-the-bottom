from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from bottom import conditions as c
from bottom.sources.earnings import EarningsReport

AS_OF = date(2026, 11, 20)


def bdays(n: int) -> pd.DatetimeIndex:
    return pd.bdate_range(end=AS_OF, periods=n)


# --- drawdown -------------------------------------------------------------------------


@pytest.mark.parametrize(("last", "inside"), [(90.1, False), (90.0, True), (89.9, True)])
def test_drawdown_threshold(cfg, last, inside):
    values = [100.0] * 299 + [last]
    dd = c.drawdown(pd.Series(values, index=bdays(300)), AS_OF, cfg)
    assert dd.in_drawdown is inside
    assert dd.pct == pytest.approx(last - 100)


def test_drawdown_ignores_data_after_as_of(cfg):
    series = pd.Series([100.0] * 10 + [50.0], index=bdays(11))
    dd = c.drawdown(series, (bdays(11)[-2]).date(), cfg)
    assert dd.pct == 0
    assert not dd.in_drawdown


# --- 1. VIX -----------------------------------------------------------------------------


@pytest.mark.parametrize(("spike", "met"), [(30.0, False), (30.01, True), (45.0, True)])
def test_vix_threshold(cfg, spike, met):
    values = [20.0] * 29 + [spike]
    result = c.vix_condition(pd.Series(values, index=bdays(30)), AS_OF, cfg)
    assert result.met is met


def test_vix_spike_outside_lookback_does_not_count(cfg):
    values = [20.0] * 30
    values[-11] = 40.0  # 11 trading days ago; the window is 10
    result = c.vix_condition(pd.Series(values, index=bdays(30)), AS_OF, cfg)
    assert result.met is False


def test_vix_missing_data(cfg):
    result = c.vix_condition(pd.Series(dtype=float), AS_OF, cfg)
    assert result.met is None
    assert not result.counts


# --- 2. Fed -----------------------------------------------------------------------------


def daily(values: list[float]) -> pd.Series:
    return pd.Series(values, index=pd.date_range(end=AS_OF, periods=len(values)))


@pytest.mark.parametrize(("two_year", "met"), [(4.35, True), (4.36, False), (3.50, True)])
def test_fed_spread(cfg, two_year, met):
    result = c.fed_condition(daily([two_year] * 100), daily([4.25] * 100), AS_OF, cfg)
    assert result.met is met


def test_fed_recent_hike_blocks(cfg):
    upper = [4.00] * 70 + [4.25] * 30  # raised 30 days ago
    result = c.fed_condition(daily([3.0] * 100), daily(upper), AS_OF, cfg)
    assert result.met is False
    assert "raised" in result.detail


def test_fed_old_hike_is_fine(cfg):
    upper = [4.00] * 20 + [4.25] * 80  # raised 80 days ago
    result = c.fed_condition(daily([3.0] * 100), daily(upper), AS_OF, cfg)
    assert result.met is True


# --- 3. Margin debt ---------------------------------------------------------------------


def months(values: list[float], last_month_end: str = "2026-10-31") -> pd.Series:
    index = pd.date_range(end=last_month_end, periods=len(values), freq="ME")
    return pd.Series(values, index=index)


@pytest.mark.parametrize(("latest", "met"), [(90.0, True), (90.1, False), (80.0, True)])
def test_margin_drop(cfg, latest, met):
    result = c.margin_condition(months([100.0] * 11 + [latest]), AS_OF, cfg)
    assert result.met is met
    assert not result.stale


def test_margin_uses_trailing_window_only(cfg):
    # A high 13 months back is outside the 12-month window.
    values = [200.0] + [100.0] * 11 + [95.0]
    result = c.margin_condition(months(values), AS_OF, cfg)
    assert result.met is False


def test_margin_stale_counts_as_not_met(cfg):
    series = months([100.0] * 11 + [80.0], last_month_end="2026-08-31")
    result = c.margin_condition(series, AS_OF, cfg)  # 81 days after the data month
    assert result.stale
    assert result.met is False
    assert not result.counts


def test_margin_publication_lag_for_past_dates(cfg):
    series = months([100.0] * 11 + [80.0], last_month_end="2026-10-31")
    as_of = date(2026, 11, 10)  # October data not yet published on this date
    with_lag = c.margin_condition(series, as_of, cfg, apply_lag=True)
    without = c.margin_condition(series, as_of, cfg, apply_lag=False)
    assert with_lag.data_date == date(2026, 9, 30)
    assert without.data_date == date(2026, 10, 31)


def weeks(gross: list[float], last: str = "2026-11-10") -> pd.DataFrame:
    index = pd.date_range(end=last, periods=len(gross), freq="7D")
    return pd.DataFrame(
        {"long": [g * 0.25 for g in gross], "short": [g * 0.75 for g in gross]}, index=index
    )


def flat_margin() -> pd.Series:
    return months([100.0] * 12)  # FINRA shows no drop


@pytest.fixture
def counting(cfg):
    """The futures rule switched on, as it will be once it is tuned."""
    cfg["margin"]["funds"]["counts"] = True
    return cfg


def test_funds_reading_is_shown_but_does_not_count_by_default(cfg):
    lev = weeks([100.0] * 26 + [80.0] * 4)
    result = c.margin_condition(flat_margin(), AS_OF, cfg, leverage=lev)
    assert result.met is False
    assert "fund futures" in result.value
    assert "does not count yet" in result.detail
    assert "futures" not in result.threshold


def test_funds_drop_alone_passes_margin(counting):
    cfg = counting
    # 4-report average falls from 100 to 80: 20% below its high.
    lev = weeks([100.0] * 26 + [80.0] * 4)
    result = c.margin_condition(flat_margin(), AS_OF, cfg, leverage=lev)
    assert result.met is True
    assert "fund futures" in result.value
    assert result.data_date == date(2026, 11, 10)


def test_funds_small_drop_does_not_pass(counting):
    cfg = counting
    lev = weeks([100.0] * 26 + [90.0] * 4)  # 10% below, threshold is 15%
    result = c.margin_condition(flat_margin(), AS_OF, cfg, leverage=lev)
    assert result.met is False


def test_one_expiry_week_dip_is_smoothed(counting):
    cfg = counting
    # Positions drop 30% for one week around a contract expiry, then recover.
    lev = weeks([100.0] * 29 + [70.0])
    result = c.margin_condition(flat_margin(), AS_OF, cfg, leverage=lev)
    assert result.met is False  # 4-week average is only 7.5% below


def test_finra_drop_passes_without_funds_data(cfg):
    result = c.margin_condition(months([100.0] * 11 + [85.0]), AS_OF, cfg, leverage=pd.DataFrame())
    assert result.met is True


def test_stale_funds_reading_does_not_count(counting):
    cfg = counting
    lev = weeks([100.0] * 26 + [80.0] * 4, last="2026-10-13")  # 38 days old
    result = c.margin_condition(flat_margin(), AS_OF, cfg, leverage=lev)
    assert result.met is False
    assert not result.stale  # FINRA is still fresh


def test_both_stale_marks_condition_stale(counting):
    cfg = counting
    lev = weeks([100.0] * 30, last="2026-10-13")
    series = months([100.0] * 12, last_month_end="2026-08-31")
    result = c.margin_condition(series, AS_OF, cfg, leverage=lev)
    assert result.stale
    assert not result.counts


def test_funds_publication_lag_for_past_dates(counting):
    cfg = counting
    lev = weeks([100.0] * 30, last="2026-11-17")  # Tuesday; published Friday Nov 20
    as_of = date(2026, 11, 18)
    with_lag = c.margin_condition(flat_margin(), as_of, cfg, apply_lag=True, leverage=lev)
    assert with_lag.data_date == date(2026, 11, 10)


# --- 4. Leading sector ------------------------------------------------------------------


def sector_closes(cfg, leader: str, twenty_day_return: float, n: int = 60) -> pd.DataFrame:
    index = bdays(n)
    frame = pd.DataFrame(100.0, index=index, columns=["SPY", *cfg["tickers"]["sectors"]])
    growth = (1 + twenty_day_return) ** (1 / 20)
    frame[leader] = 100.0 * growth ** np.arange(n)
    return frame


@pytest.mark.parametrize(("ret", "met"), [(0.06, True), (0.05, True), (0.04, False)])
def test_sector_excess_threshold(cfg, ret, met):
    result = c.sector_condition(sector_closes(cfg, "XLE", ret), AS_OF, cfg)
    assert result.met is met
    assert result.value.startswith("XLE")


def test_leader_persistence():
    rows = [{"XLK": 6.0, "XLE": 1.0}] * 6 + [{"XLK": 1.0, "XLE": 6.0}] * 9
    leader, pts, days = c.leader_stats(pd.DataFrame(rows))
    assert (leader, pts, days) == ("XLE", 6.0, 9)


def test_sector_needs_persistence(cfg):
    frame = sector_closes(cfg, "XLE", 0.06)
    frame.iloc[-15:-9, frame.columns.get_loc("XLK")] = 1000.0  # XLK on top for 6 days
    frame.iloc[-9:, frame.columns.get_loc("XLK")] = 100.0
    result = c.sector_condition(frame, AS_OF, cfg)
    assert result.met is False


# --- 5. NVIDIA earnings -----------------------------------------------------------------


def no_drawdown(cfg) -> c.Drawdown:
    return c.Drawdown(-2.0, AS_OF - timedelta(days=30), False, AS_OF, -10)


def test_nvda_recent_beat(cfg):
    reports = [EarningsReport(AS_OF - timedelta(days=85), 1.10, 1.00)]
    assert c.nvda_condition(reports, AS_OF, no_drawdown(cfg), cfg).met is True


def test_nvda_miss(cfg):
    reports = [EarningsReport(AS_OF - timedelta(days=10), 0.90, 1.00)]
    assert c.nvda_condition(reports, AS_OF, no_drawdown(cfg), cfg).met is False


def test_nvda_old_report_outside_window(cfg):
    reports = [EarningsReport(AS_OF - timedelta(days=100), 1.10, 1.00)]
    result = c.nvda_condition(reports, AS_OF, no_drawdown(cfg), cfg)
    assert result.met is False
    assert "outside" in result.detail


def test_nvda_report_since_drawdown_began_counts(cfg):
    dd = c.Drawdown(-15.0, AS_OF - timedelta(days=120), True, AS_OF, -10)
    reports = [EarningsReport(AS_OF - timedelta(days=100), 1.10, 1.00)]
    assert c.nvda_condition(reports, AS_OF, dd, cfg).met is True


def test_nvda_ignores_future_reports(cfg):
    reports = [
        EarningsReport(AS_OF - timedelta(days=10), 0.90, 1.00),
        EarningsReport(AS_OF + timedelta(days=5), 2.00, 1.00),
    ]
    assert c.nvda_condition(reports, AS_OF, no_drawdown(cfg), cfg).met is False

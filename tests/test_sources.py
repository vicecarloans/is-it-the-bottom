import io
from datetime import date, datetime

import openpyxl
import pytest

from bottom.sources import cftc, earnings, finra, fred, prices


def make_xlsx(rows: list[list[object]]) -> bytes:
    book = openpyxl.Workbook()
    sheet = book.active
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


HEADER = [
    "Month/Year",
    "Debit Balances in Customers' Securities Margin Accounts",
    "Free Credit Balances in Customers' Cash Accounts",
    "Free Credit Balances in Customers' Securities Margin Accounts",
]


def test_finra_text_months_with_title_row():
    content = make_xlsx(
        [
            ["FINRA Margin Statistics"],
            HEADER,
            ["Oct-26", 1448360, 210290, 212890],
            ["Sep-26", "1,480,054", 215328, 218581],
            ["Aug-26", 1453832, 207641, 217499],
        ]
    )
    series = finra.parse_margin_xlsx(content)
    assert list(series.index.strftime("%Y-%m-%d")) == ["2026-08-31", "2026-09-30", "2026-10-31"]
    assert series.iloc[-1] == 1448360
    assert series.iloc[1] == 1480054


def test_finra_spreadsheet_format():
    """The downloaded file has a Year-Month header and months like 2026-10, newest first."""
    header = ["Year-Month", *HEADER[1:]]
    content = make_xlsx([header, ["2026-10", 1448360, 1, 1], ["2026-09", 1480054, 1, 1]])
    series = finra.parse_margin_xlsx(content)
    assert [d.isoformat() for d in series.index.date] == ["2026-09-30", "2026-10-31"]
    assert series.iloc[-1] == 1448360


def test_finra_date_cells():
    content = make_xlsx([HEADER, [datetime(2026, 10, 1), 1448360, 1, 1]])
    series = finra.parse_margin_xlsx(content)
    assert series.index[0].date() == date(2026, 10, 31)


def test_finra_falls_back_to_local_copy(tmp_path, monkeypatch):
    monkeypatch.setattr(finra, "_download", lambda client, url, label, reasons: None)
    monkeypatch.setattr(finra, "_find_xlsx_link", lambda client, reasons: None)
    copy = tmp_path / "margin-statistics.xlsx"
    copy.write_bytes(make_xlsx([HEADER, ["Oct-26", 1448360, 1, 1]]))
    series = finra.fetch_margin(local_copy=copy)
    assert series.iloc[-1] == 1448360


def test_finra_reports_why_it_failed(tmp_path, monkeypatch):
    def refused(client, url, label, reasons):
        reasons.append(f"{label}: HTTP 403")

    monkeypatch.setattr(finra, "_download", refused)
    monkeypatch.setattr(finra, "_find_xlsx_link", lambda client, reasons: None)
    with pytest.raises(RuntimeError, match="HTTP 403"):
        finra.fetch_margin(local_copy=tmp_path / "missing.xlsx")


def test_finra_missing_header():
    with pytest.raises(ValueError):
        finra.parse_margin_xlsx(make_xlsx([["nothing", "here"], [1, 2]]))


def test_fred_skips_missing_values():
    payload = {
        "observations": [
            {"date": "2026-11-18", "value": "19.3"},
            {"date": "2026-11-19", "value": "."},
            {"date": "2026-11-20", "value": "21.0"},
        ]
    }
    series = fred.parse_observations(payload)
    assert list(series) == [19.3, 21.0]


def test_finnhub_calendar_skips_upcoming():
    payload = {
        "earningsCalendar": [
            {
                "date": "2026-08-26",
                "epsActual": 1.05,
                "epsEstimate": 1.01,
                "quarter": 2,
                "year": 2027,
                "symbol": "NVDA",
            },
            {
                "date": "2026-11-25",
                "epsActual": None,
                "epsEstimate": 1.20,
                "quarter": 3,
                "year": 2027,
                "symbol": "NVDA",
            },
            {
                "date": "2026-05-27",
                "epsActual": 0.90,
                "epsEstimate": 0.93,
                "quarter": 1,
                "year": 2027,
                "symbol": "NVDA",
            },
        ]
    }
    reports = earnings.parse_calendar(payload)
    assert [r.date for r in reports] == [date(2026, 5, 27), date(2026, 8, 26)]
    assert reports[-1].beat and not reports[0].beat
    assert reports[-1].period == "Q2 FY2027"


def test_finnhub_history_estimates_report_date():
    payload = [{"actual": 1.05, "estimate": 1.01, "period": "2026-07-31", "symbol": "NVDA"}]
    (report,) = earnings.parse_history(payload)
    assert report.approx_date
    assert report.date == date(2026, 8, 28)


def test_stooq_csv():
    text = "Date,Open,High,Low,Close,Volume\n2026-11-19,1,1,1,600.5,10\n2026-11-20,1,1,1,601,10\n"
    series = prices.parse_stooq_csv(text, date(2026, 11, 20))
    assert list(series) == [601.0]


def test_stooq_has_no_indexes():
    assert prices.stooq_symbol("^VIX") is None
    assert prices.stooq_symbol("XLRE") == "xlre.us"


def test_cftc_parses_rows_and_skips_bad_ones():
    rows = [
        {
            "report_date_as_yyyy_mm_dd": "2026-11-10T00:00:00.000",
            "lev_money_positions_long": "145755",
            "lev_money_positions_short": "448815",
        },
        {
            "report_date_as_yyyy_mm_dd": "2026-11-03T00:00:00.000",
            "lev_money_positions_long": "125269",
            "lev_money_positions_short": "497587",
        },
        {"report_date_as_yyyy_mm_dd": "2026-10-27T00:00:00.000"},
    ]
    frame = cftc.parse_tff(rows)
    assert list(frame.index.strftime("%Y-%m-%d")) == ["2026-11-03", "2026-11-10"]
    assert frame.loc["2026-11-10", "short"] == 448815.0


def test_cftc_empty_response_raises():
    with pytest.raises(RuntimeError):
        cftc.parse_tff([])

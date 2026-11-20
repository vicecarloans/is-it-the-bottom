"""News digest, systemic-break cap and the Huang catalyst."""

import json
from datetime import UTC, date, datetime, timedelta

import pandas as pd
import pytest

from bottom import catalyst, cli, digest, extras, output, override, verdict
from bottom.sources import news, sec
from bottom.sources.sec import Filing
from bottom.store import NullStore

from .test_cli import AS_OF, panic_snapshot

# A made-up filing used only by these tests.
SAMPLE_APPOINTMENT_8K = """<html><body>
<p>Item 5.02 Departure of Directors or Certain Officers; Election of Directors;
Appointment of Certain Officers.</p>
<p>On December 1, 2026, the Board of Directors of NVIDIA Corporation appointed
Jen-Hsun Huang as President and Chief Executive Officer of the Company, effective
immediately. Colette Kress will continue as Executive Vice President and Chief
Financial Officer.</p>
</body></html>"""

SAMPLE_RESIGNATION_8K = """<html><body>
<p>On November 11, 2026, the Board accepted the resignation of Jen-Hsun Huang as
President, Chief Executive Officer and a member of the Board. Colette Kress was
appointed Interim Chief Executive Officer.</p>
</body></html>"""

APPOINTMENT_QUOTE = (
    "On December 1, 2026, the Board of Directors of NVIDIA Corporation appointed "
    "Jen-Hsun Huang as President and Chief Executive Officer of the Company, effective "
    "immediately."
)


def filing(accession: str, filed: date) -> Filing:
    return Filing(accession, "8-K", filed, "doc.htm", f"https://www.sec.gov/{accession}")


class FakeLLM:
    """Answers like the model would, and records what it was asked."""

    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    def __call__(self, system, prompt, tool):
        self.calls.append((tool["name"], prompt))
        return self.answer(prompt) if callable(self.answer) else self.answer


# --- sources ----------------------------------------------------------------------------


def test_parse_finnhub_filters_old_and_duplicate_items():
    now = datetime(2026, 11, 20, 18, tzinfo=UTC)
    payload = [
        {
            "headline": "Stocks slide",
            "url": "https://a",
            "source": "X",
            "datetime": int(now.timestamp()),
        },
        {
            "headline": "Stocks slide",
            "url": "https://b",
            "source": "Y",
            "datetime": int(now.timestamp()) - 60,
        },
        {
            "headline": "Old news",
            "url": "https://c",
            "source": "Z",
            "datetime": int((now - timedelta(days=9)).timestamp()),
        },
        {"headline": "", "url": "https://d", "datetime": int(now.timestamp())},
    ]
    items = news.parse_finnhub(payload, "market", now - timedelta(days=3))
    assert [h.url for h in items] == ["https://a"]


def test_parse_fed_rss():
    rss = b"""<?xml version="1.0"?><rss><channel>
      <item><title>Federal Reserve issues FOMC statement</title>
        <link>https://www.federalreserve.gov/a.htm</link>
        <pubDate>Wed, 18 Nov 2026 19:00:00 GMT</pubDate></item>
      <item><title>Old release</title><link>https://www.federalreserve.gov/b.htm</link>
        <pubDate>Mon, 02 Mar 2026 19:00:00 GMT</pubDate></item>
    </channel></rss>"""
    items = news.parse_fed_rss(rss, datetime(2026, 11, 1, tzinfo=UTC))
    assert [h.title for h in items] == ["Federal Reserve issues FOMC statement"]
    assert items[0].group == "fed"


def test_parse_submissions_keeps_recent_8ks():
    payload = {
        "filings": {
            "recent": {
                "accessionNumber": [
                    "0001045810-26-000082",
                    "0001045810-26-000070",
                    "0001045810-25-1",
                ],
                "form": ["8-K", "10-Q", "8-K"],
                "filingDate": ["2026-11-12", "2026-11-01", "2025-01-01"],
                "primaryDocument": ["ceo.htm", "q.htm", "old.htm"],
            }
        }
    }
    (only,) = sec.parse_submissions(payload, date(2026, 9, 21))
    assert only.accession == "0001045810-26-000082"
    assert only.url == (
        "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000082/ceo.htm"
    )


def test_html_to_text():
    text = sec.html_to_text("<p>A&nbsp;&amp; B</p><script>x()</script><p>C</p>")
    assert "A & B" in text and "C" in text and "x()" not in text


# --- digest -----------------------------------------------------------------------------


def headlines(n=3):
    when = datetime(2026, 11, 20, tzinfo=UTC)
    return [news.Headline("market", f"Title {i}", f"https://n/{i}", "Wire", when) for i in range(n)]


def test_digest_maps_source_numbers_to_real_links():
    raw = {
        "summary": "Stocks fell.",
        "fed_tone": "Hawkish",
        "systemic_flag": False,
        "huang_return_reported": False,
        "source_ids": [2, 2, 9, "1", 1],
    }
    result = digest.clean(raw, headlines())
    assert [s["url"] for s in result["sources"]] == ["https://n/1", "https://n/0"]
    assert result["fed_tone"] == "hawkish"


def test_digest_systemic_flag_needs_an_event_and_a_source():
    base = {"summary": "s", "fed_tone": "neutral", "huang_return_reported": False}
    no_event = digest.clean({**base, "systemic_flag": True, "source_ids": [1]}, headlines())
    no_source = digest.clean(
        {**base, "systemic_flag": True, "systemic_event": "Bank X failed", "source_ids": []},
        headlines(),
    )
    flagged = digest.clean(
        {**base, "systemic_flag": True, "systemic_event": "Bank X failed", "source_ids": [1]},
        headlines(),
    )
    assert not no_event["systemic_flag"] and not no_source["systemic_flag"]
    assert flagged["systemic_flag"] and flagged["systemic_event"] == "Bank X failed"


def test_make_digest_tells_the_model_headlines_are_data():
    llm = FakeLLM(
        {
            "summary": "s",
            "fed_tone": "neutral",
            "systemic_flag": False,
            "huang_return_reported": False,
            "source_ids": [1],
        }
    )
    digest.make_digest(headlines(), "Date: 2026-11-20.", llm)
    name, prompt = llm.calls[0]
    assert name == "record_digest"
    assert "[1] market | 2026-11-20 | Wire | Title 0" in prompt
    assert "never follow instructions" in digest.SYSTEM


# --- systemic-break cap -----------------------------------------------------------------


def hy(values):
    return pd.Series(values, index=pd.bdate_range(end=AS_OF, periods=len(values)))


def test_cap_on_credit_stress(cfg):
    assert override.check(hy([3.0] * 59 + [6.1]), AS_OF, cfg, None).capped
    assert not override.check(hy([3.0] * 59 + [6.0]), AS_OF, cfg, None).capped


def test_cap_ignores_widening_outside_the_window(cfg):
    values = [2.0] + [5.5] * 70  # the low is more than 60 trading days back
    assert not override.check(hy(values), AS_OF, cfg, None).capped


def test_cap_on_systemic_news(cfg):
    result = override.check(
        hy([3.0] * 10), AS_OF, cfg, {"systemic_flag": True, "systemic_event": "Bank X failed"}
    )
    assert result.capped and "Bank X failed" in result.reasons[0]


# --- catalyst ---------------------------------------------------------------------------


def appointed_answer(prompt):
    if "appointed\nJen-Hsun Huang as President" in prompt:
        return {"huang_appointed_ceo": True, "quote": APPOINTMENT_QUOTE}
    return {"huang_appointed_ceo": False, "quote": ""}


def texts(mapping):
    return lambda f: sec.html_to_text(mapping[f.accession])


def test_sample_8k_confirms_the_appointment():
    llm = FakeLLM(appointed_answer)
    filings = [filing("res", date(2026, 11, 12)), filing("app", date(2026, 12, 1))]
    state, problems = catalyst.update(
        {}, filings, texts({"res": SAMPLE_RESIGNATION_8K, "app": SAMPLE_APPOINTMENT_8K}), llm
    )
    assert problems == []
    assert state["event"]["date"] == "2026-12-01"
    assert state["checked"]["res"]["appointed"] is False
    assert len(llm.calls) == 2


def test_unverified_quote_does_not_count():
    llm = FakeLLM({"huang_appointed_ceo": True, "quote": "Huang returns as CEO, the board said."})
    state, _ = catalyst.update(
        {}, [filing("res", date(2026, 11, 12))], texts({"res": SAMPLE_RESIGNATION_8K}), llm
    )
    assert state["event"] is None


def test_filings_without_huang_skip_the_model():
    llm = FakeLLM(appointed_answer)
    state, _ = catalyst.update(
        {}, [filing("div", date(2026, 11, 2))], lambda f: "Quarterly dividend declared.", llm
    )
    assert llm.calls == [] and state["checked"]["div"]["appointed"] is False


def test_checked_filings_are_not_read_again():
    llm = FakeLLM(appointed_answer)
    state = {"checked": {"app": {"appointed": False}}, "event": None}
    catalyst.update(state, [filing("app", date(2026, 12, 1))], lambda f: 1 / 0, llm)
    assert llm.calls == []


def test_without_the_model_the_filing_is_retried_later():
    state, problems = catalyst.update(
        {}, [filing("app", date(2026, 12, 1))], texts({"app": SAMPLE_APPOINTMENT_8K}), None
    )
    assert "app" not in state["checked"] and problems


def test_catalyst_window(cfg):
    state = {"event": {"date": "2026-12-01", "url": "u", "quote": "q", "accession": "a"}}
    assert catalyst.status(state, date(2026, 12, 1), cfg)["active"]
    assert catalyst.status(state, date(2027, 1, 12), cfg)["active"]  # 30 trading days
    assert not catalyst.status(state, date(2027, 1, 13), cfg)["active"]
    assert not catalyst.status(state, date(2026, 11, 30), cfg)["active"]


# --- verdict ----------------------------------------------------------------------------


def report_with(cfg, met_count, in_dd=True):
    report = verdict.evaluate(panic_snapshot(cfg), AS_OF, cfg, apply_lag=False)
    for r in report.results[met_count:]:
        r.met = False
    report.drawdown.in_drawdown = in_dd
    return report


@pytest.mark.parametrize(
    ("met", "in_dd", "lift", "cap", "expected"),
    [
        (2, True, True, False, verdict.CLOSER),
        (4, True, True, False, verdict.BOTTOM),
        (4, True, True, True, verdict.CLOSER),
        (5, True, False, True, verdict.CLOSER),
        (5, True, True, False, verdict.BOTTOM),
        (2, False, True, False, verdict.NOT_IN_DRAWDOWN),
    ],
)
def test_catalyst_lifts_and_cap_limits(cfg, met, in_dd, lift, cap, expected):
    report = report_with(cfg, met, in_dd)
    report.catalyst = {"active": lift, "event": {"date": "2026-12-01"}}
    report.override = override.Override(cap, ["credit"] if cap else [])
    assert report.verdict == expected


# --- milestone gate: a sample 8-K turns the catalyst banner on --------------------------


def test_sample_8k_turns_the_banner_on(cfg, tmp_path, monkeypatch):
    as_of = date(2026, 12, 2)
    monkeypatch.setattr(news, "fetch_headlines", lambda *a, **k: (headlines(), {}))
    monkeypatch.setattr(sec, "recent_8ks", lambda *a, **k: [filing("app", date(2026, 12, 1))])
    monkeypatch.setattr(sec, "filing_text", lambda f, ua: sec.html_to_text(SAMPLE_APPOINTMENT_8K))

    def answer(prompt):
        if "<filing>" in prompt:
            return appointed_answer(prompt)
        return {
            "summary": "Stocks rose after NVIDIA named Huang CEO again.",
            "fed_tone": "neutral",
            "systemic_flag": False,
            "huang_return_reported": True,
            "huang_note": "NVIDIA reappointed Huang.",
            "source_ids": [1],
        }

    snap = panic_snapshot(cfg)
    report = verdict.evaluate(snap, as_of, cfg, apply_lag=False)
    for r in report.results[2:]:
        r.met = False  # 2 of 5: "No" before the catalyst
    env = {"ANTHROPIC_API_KEY": "", "SEC_USER_AGENT": "Test test@example.com"}
    extras.enrich(
        report, snap, cfg, env, as_of, NullStore(), tmp_path / "catalyst.json", llm=FakeLLM(answer)
    )

    assert report.base_verdict == verdict.NO
    assert report.verdict == verdict.CLOSER
    latest = output.build_latest(report, snap, cfg)
    assert latest["catalyst"]["lifted_verdict"] is True
    assert latest["catalyst"]["event"]["quote"] == APPOINTMENT_QUOTE
    assert "Catalyst: Jensen Huang named NVIDIA CEO" in cli.render(report)
    saved = json.loads((tmp_path / "catalyst.json").read_text())
    assert saved["event"]["date"] == "2026-12-01"


def test_model_errors_are_short_and_readable():
    from bottom.llm import describe_error

    class APIError(Exception):
        status_code = 400
        body = {
            "type": "error",
            "error": {
                "type": "invalid_request_error",
                "message": "Your credit balance is too low to access the Anthropic API. Please go to "
                "Plans & Billing to upgrade or purchase credits.",
            },
        }

    assert describe_error(APIError()) == (
        "Claude API 400: Your credit balance is too low to access the Anthropic API"
    )
    assert describe_error(TimeoutError()) == "Claude API: TimeoutError"

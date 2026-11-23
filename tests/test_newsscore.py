import json
from datetime import date

import pandas as pd
import pytest

from bottom import newsscore


@pytest.fixture
def ncfg(cfg):
    cfg = dict(cfg)
    cfg["news"] = {**cfg["news"], "lines": {"XLK": "Technology", "XLE": "Energy"}}
    return cfg


def story(
    url,
    published,
    macro="neutral",
    sectors=(),
    novelty="new",
    confidence="high",
    story_id=1,
    channel="growth",
):
    return {
        "id": story_id,
        "story": story_id,
        "feed": "market",
        "title": url,
        "url": url,
        "source": "Wire",
        "published": published,
        "tags": {
            "macro": macro,
            "channel": channel,
            "novelty": novelty,
            "confidence": confidence,
            "sectors": [{"line": line, "direction": d, "strength": s} for line, d, s in sectors],
        },
    }


def closes(rows):
    """rows: {date: (SPY, XLK, XLE)}"""
    frame = pd.DataFrame.from_dict(rows, orient="index", columns=["SPY", "XLK", "XLE"])
    frame.index = pd.to_datetime(frame.index)
    return frame


PRICES = closes(
    {
        "2026-11-19": (100, 100, 100),
        "2026-11-20": (100, 100, 100),
        "2026-11-23": (101, 104, 99),  # SPY +1%, XLK +4% (3 pts vs SPY), XLE -1% (-2 pts)
    }
)


def test_reaction_uses_the_closes_around_the_story(ncfg):
    # Published Sunday Nov 22: last close before is Fri Nov 20, first after is Mon Nov 23.
    s = story(
        "a",
        "2026-11-22T15:00+00:00",
        macro="positive",
        sectors=[("XLK", "positive", 2), ("XLE", "positive", 1)],
    )
    out = newsscore.score([{**s, "count": 1}], PRICES, date(2026, 11, 23), ncfg)
    xlk, xle = out["lines"]["XLK"], out["lines"]["XLE"]
    assert xlk["top"][0]["reaction"] == "confirmed" and xlk["top"][0]["change"] == pytest.approx(
        3.0
    )
    assert xle["top"][0]["reaction"] == "faded"  # good news, fell vs the index
    assert out["macro"]["top"][0]["reaction"] == "confirmed"
    assert xlk["score"] == pytest.approx(2.0)  # 2 x high confidence x new, no decay on day 0


def test_story_after_the_last_close_is_pending(ncfg):
    s = story("b", "2026-11-23T22:30+00:00", sectors=[("XLK", "negative", 3)])  # 5:30 pm Monday
    out = newsscore.score([{**s, "count": 1}], PRICES, date(2026, 11, 23), ncfg)
    assert out["lines"]["XLK"]["reactions"]["pending"] == 1
    assert out["lines"]["XLK"]["score"] == pytest.approx(-3.0)


def test_small_moves_are_muted_and_old_news_decays(ncfg):
    prices = closes(
        {
            "2026-11-13": (100, 100, 100),
            "2026-11-16": (100, 100, 100),
            "2026-11-17": (100, 100.2, 100),
            "2026-11-18": (100, 100, 100),
            "2026-11-19": (100, 100, 100),
            "2026-11-20": (100, 100, 100),
        }
    )
    s = story("c", "2026-11-16T18:00+00:00", sectors=[("XLK", "positive", 2)])  # Monday 1 pm
    out = newsscore.score([{**s, "count": 1}], prices, date(2026, 11, 20), ncfg)
    assert out["lines"]["XLK"]["top"][0]["reaction"] == "muted"
    assert out["lines"]["XLK"]["score"] == pytest.approx(
        2 * 0.5 ** (4 / 3), abs=0.01
    )  # 4 trading days later


def test_rehash_counts_for_nothing_and_follow_up_for_half(ncfg):
    stories = [
        {
            **story(
                "r", "2026-11-22T15:00+00:00", sectors=[("XLK", "positive", 3)], novelty="rehash"
            ),
            "count": 1,
        },
        {
            **story(
                "f",
                "2026-11-22T15:00+00:00",
                sectors=[("XLK", "positive", 2)],
                novelty="follow_up",
                confidence="low",
            ),
            "count": 1,
        },
    ]
    out = newsscore.score(stories, PRICES, date(2026, 11, 23), ncfg)
    assert out["lines"]["XLK"]["stories"] == 1
    assert out["lines"]["XLK"]["score"] == pytest.approx(2 * 0.5 * 0.5)


def test_load_stories_merges_days_and_counts_each_story_once(tmp_path):
    news = tmp_path / "news"
    news.mkdir()
    old = [
        story("u1", "2026-11-20T15:00+00:00", story_id=1),
        story("u2", "2026-11-20T16:00+00:00", story_id=1),
    ]
    (news / "2026-11-20.json").write_text(json.dumps({"headlines": old}))
    (news / "2026-11-23.json").write_text(
        json.dumps({"headlines": [story("stale", "2026-11-23T15:00+00:00")]})
    )
    today = [
        story("u1", "2026-11-20T15:00+00:00", story_id=5),
        story("u3", "2026-11-23T14:00+00:00", story_id=6),
        {**story("u4", "2026-11-23T14:00+00:00", story_id=7), "tags": None},
    ]
    stories = newsscore.load_stories(news, today, date(2026, 11, 23), 21)
    assert [(s["url"], s["count"]) for s in stories] == [
        ("u1", 2),
        ("u3", 1),
    ]  # u1 seen before; today's file replaced


def test_tone_labels(cfg):
    s = cfg["news"]["scoring"]
    assert [newsscore.tone(v, s) for v in (5, 2, 0, -2, -5)] == [
        "strongly positive",
        "positive",
        "mixed or quiet",
        "negative",
        "strongly negative",
    ]


def test_lagging_line_with_good_news_is_flagged(ncfg):
    days = pd.bdate_range("2026-10-20", "2026-11-23")
    frame = pd.DataFrame({"SPY": 100.0, "XLK": 100.0, "XLE": 100.0}, index=days)
    frame.loc[days[-1], ["SPY", "XLK", "XLE"]] = [110, 105, 120]  # XLK lags by 5, XLE leads by 10
    stories = [
        {**story("g", "2026-11-22T15:00+00:00", sectors=[("XLK", "positive", 3)]), "count": 1},
        {
            **story("b", "2026-11-22T15:00+00:00", sectors=[("XLE", "negative", 3)], story_id=2),
            "count": 1,
        },
    ]
    out = newsscore.score(stories, frame, date(2026, 11, 23), ncfg)
    assert out["lines"]["XLK"]["excess_pct"] == pytest.approx(-5.0)
    assert out["lines"]["XLK"]["divergence"] == "good_news_lagging"
    assert out["lines"]["XLE"]["divergence"] == "bad_news_leading"
    assert out["window_days"] == ncfg["sector"]["return_window_days"]


def test_opinion_counts_a_quarter(ncfg):
    s = story("o", "2026-11-22T15:00+00:00", sectors=[("XLK", "negative", 3)])
    s["tags"]["kind"] = "opinion"
    out = newsscore.score([{**s, "count": 1}], PRICES, date(2026, 11, 23), ncfg)
    assert out["lines"]["XLK"]["score"] == pytest.approx(-0.75)
    assert out["lines"]["XLK"]["top"][0]["kind"] == "opinion"


def test_top_story_agrees_with_the_line_total(ncfg):
    big_against = story(
        "against", "2026-11-22T15:00+00:00", sectors=[("XLK", "negative", 3)], story_id=1
    )
    small_with = story(
        "with", "2026-11-22T15:00+00:00", sectors=[("XLK", "positive", 2)], story_id=2
    )
    more_with = story(
        "more", "2026-11-22T15:00+00:00", sectors=[("XLK", "positive", 2)], story_id=3
    )
    out = newsscore.score(
        [{**s, "count": 1} for s in (big_against, small_with, more_with)],
        PRICES,
        date(2026, 11, 23),
        ncfg,
    )
    xlk = out["lines"]["XLK"]
    assert xlk["score"] > 0
    assert [t["title"] for t in xlk["top"]][:2] == ["with", "more"]
    assert xlk["top"][2]["title"] == "against"

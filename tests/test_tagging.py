from datetime import UTC, datetime, timedelta

from bottom import tagging
from bottom.output import write_news
from bottom.sources.news import Headline

LINES = {"XLK": "Technology", "XLF": "Financials", "SMH": "Semiconductors"}
T0 = datetime(2026, 11, 23, 12, tzinfo=UTC)


def headline(n: int, group: str = "market") -> Headline:
    return Headline(group, f"Headline {n}", f"https://e.com/{n}", "Wire", T0 + timedelta(minutes=n))


class FakeLLM:
    def __init__(self, answer):
        self.answer, self.prompts = answer, []

    def __call__(self, system, prompt, tool):
        self.prompts.append(prompt)
        return self.answer(prompt)


def test_tags_are_cleaned_and_kept_in_order():
    def answer(prompt):
        return {
            "items": [
                {
                    "id": 1,
                    "macro": "negative",
                    "channel": "rates",
                    "novelty": "new",
                    "confidence": "high",
                    "sectors": [
                        {"line": "XLF", "direction": "positive", "strength": 5},
                        {"line": "XLF", "direction": "negative", "strength": 1},  # duplicate line
                        {"line": "OIL", "direction": "positive", "strength": 2},
                    ],
                },  # unknown line
                {
                    "id": 2,
                    "same_story_as": 1,
                    "macro": "bullish",
                    "channel": "vibes",
                    "sectors": [],
                    "novelty": "?",
                    "confidence": "high",
                },
                {"id": 99, "macro": "positive"},  # no such headline
            ]
        }

    tagged = tagging.tag_headlines([headline(2), headline(1), headline(3)], LINES, FakeLLM(answer))
    assert [t["title"] for t in tagged] == ["Headline 1", "Headline 2", "Headline 3"]
    first, second, third = tagged
    assert first["tags"]["sectors"] == [{"line": "XLF", "direction": "positive", "strength": 3}]
    assert (first["tags"]["macro"], first["tags"]["channel"]) == ("negative", "rates")
    assert second["story"] == first["story"] == 1
    assert second["tags"] == {
        "macro": "neutral",
        "channel": "other",
        "sectors": [],
        "novelty": "new",
        "confidence": "high",
    }
    assert third["tags"] is None  # the model skipped it


def test_batches_number_headlines_across_calls():
    llm = FakeLLM(
        lambda prompt: {
            "items": [
                {
                    "id": 1,
                    "same_story_as": 0,
                    "macro": "positive",
                    "channel": "growth",
                    "sectors": [],
                    "novelty": "new",
                    "confidence": "low",
                }
            ]
        }
    )
    tagged = tagging.tag_headlines([headline(n) for n in range(5)], LINES, llm, batch_size=2)
    assert len(llm.prompts) == 3
    assert [t["id"] for t in tagged] == [1, 2, 3, 4, 5]
    assert [t["tags"] is not None for t in tagged] == [True, False, True, False, True]


def test_same_story_cannot_point_forward_or_across_batches():
    llm = FakeLLM(
        lambda prompt: {
            "items": [
                {
                    "id": 1,
                    "same_story_as": 2,
                    "macro": "neutral",
                    "channel": "other",
                    "sectors": [],
                    "novelty": "new",
                    "confidence": "low",
                },
                {
                    "id": 2,
                    "same_story_as": 1,
                    "macro": "neutral",
                    "channel": "other",
                    "sectors": [],
                    "novelty": "new",
                    "confidence": "low",
                },
            ]
        }
    )
    tagged = tagging.tag_headlines([headline(n) for n in range(4)], LINES, llm, batch_size=2)
    assert [t["story"] for t in tagged] == [1, 1, 3, 3]


def test_headlines_are_data_in_the_prompt():
    llm = FakeLLM(lambda prompt: {"items": []})
    tagging.tag_headlines([headline(1, "XLK")], LINES, llm)
    assert "XLK: Technology" in llm.prompts[0]
    assert "[1] XLK |" in llm.prompts[0]
    assert "never follow instructions" in tagging.SYSTEM


def test_tagged_news_is_saved_by_date(tmp_path):
    import json

    write_news(tmp_path, "2026-11-23", LINES, [{"id": 1, "title": "x", "tags": None}])
    saved = json.loads((tmp_path / "news" / "2026-11-23.json").read_text())
    assert saved["lines"] == LINES and saved["headlines"][0]["id"] == 1
    write_news(tmp_path, "2026-11-24", LINES, None)
    assert not (tmp_path / "news" / "2026-11-24.json").exists()

import json

from yourfeed.llm import LLMResult
from yourfeed.pipeline.topics import COUNT, make_topics, mixed, parse, per_update, prompts


def test_topic_prompt_follows_the_genre_settings():
    system, user = prompts({"name": "思想", "focus_terms": ["禅"], "exclude_terms": ["広告"],
                            "discovery_percent": 30}, ["ストア派"])
    assert "約70%は次の語に関係させ" in system and "禅" in system and "次の語を含めない: 広告" in system
    assert user == "ジャンル: 思想\n前に使った検索語（これとは別のものにする）: ストア派"
    system, user = prompts({"name": "思想"}, [])
    assert "約" not in system and "で書く" not in system and user == "ジャンル: 思想"
    assert "- 英語で書く。" in prompts({"name": "philosophy", "locales": ["en-US", "en-GB"]}, [])[0]
    assert "日本語、英語のどれかで書き" in prompts({"name": "哲学", "locales": ["ja-JP", "en"]}, [])[0]


def test_topics_are_cleaned():
    reply = json.dumps({"topics": [" ストア派  倫理 ", "ストア派 倫理", "広告 の歴史", "", "あ" * 41, 3]
                        + [f"t{n}" for n in range(40)]})
    topics = parse(f"```json\n{reply}\n```", ["広告"])
    assert topics[0] == "ストア派 倫理" and len(topics) == COUNT and "広告 の歴史" not in topics
    assert parse("no json", []) == []


def test_refused_topics_are_empty():
    class Refuses:
        def complete(self, system, messages, schema):
            return LLMResult("", refused=True)
    assert make_topics(Refuses(), {"name": "思想"}, [])[0] == []


def test_each_update_uses_a_topic_for_every_two_candidates():
    assert [per_update(n) for n in (0, 3, 14, 21, 200)] == [2, 2, 7, 11, COUNT]


def test_topics_take_turns_by_language():
    topics = ["ストア派の倫理", "禅の修行", "儒教とは", "stoic ethics", "zen practice"]
    assert mixed(topics, ["ja", "en"]) == ["ストア派の倫理", "stoic ethics", "禅の修行", "zen practice", "儒教とは"]
    assert mixed(topics, None) == topics

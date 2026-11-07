"""Search topics for genres of any age: the model lists subtopics and each update searches some of them."""

from __future__ import annotations

from typing import Any

from yourfeed.llm import LLMClient, LLMResult
from yourfeed.search.language import NAMES, in_language, languages_of
from yourfeed.search.plan import TAKE

from .select import matches
from .validate import parse_json

COUNT = 30
MIN_PER_UPDATE = 2
TOPIC_MAX = 40

SCHEMA = {
    "type": "object",
    "properties": {"topics": {"type": "array", "items": {"type": "string"}}},
    "required": ["topics"],
    "additionalProperties": False,
}


def prompts(genre: dict[str, Any], used: list[str]) -> tuple[str, str]:
    focus = [term for term in genre.get("focus_terms") or [] if term.strip()]
    exclude = [term for term in genre.get("exclude_terms") or [] if term.strip()]
    lines = [
        "あなたは、ニュースフィードに載せる記事を探す編集者です。",
        f"ジャンルについて、長く読める記事（解説、入門、研究、考え方、実践の方法など）を探す検索語を{COUNT}個作ります。",
        "JSONの topics に検索語の配列を返します。",
        "",
        "規則:",
        f"- 1つは{TOPIC_MAX}字以内の短い語句。それだけで検索して、このジャンルの記事が見つかるもの。",
        "- 互いに別の小テーマにし、基本的な話題から専門的な話題まで混ぜる。",
        "- 時事の話題、人の近況、商品名は使わない。",
    ]
    names = [NAMES[language] for language in languages_of(list(genre.get("locales") or ["all"])) or []
             if language in NAMES]
    if len(names) == 1:
        lines.append(f"- {names[0]}で書く。")
    elif names:
        lines.append(f"- 各検索語は{'、'.join(names)}のどれかで書き、言語ごとにほぼ同じ数にする。")
    if focus:
        lines.append(f"- 約{100 - genre.get('discovery_percent', 20)}%は次の語に関係させ、"
                     f"残りはジャンル全体から選ぶ: {'、'.join(focus)}")
    if exclude:
        lines.append(f"- 次の語を含めない: {'、'.join(exclude)}")
    user = f"ジャンル: {genre['name']}"
    if used:
        user += f"\n前に使った検索語（これとは別のものにする）: {'、'.join(used)}"
    return "\n".join(lines), user


def per_update(candidates: int) -> int:
    """Topics to search in one update: one for each TAKE candidates, so no topic fills the genre."""
    return max(MIN_PER_UPDATE, min(COUNT, -(-candidates // TAKE)))


def mixed(topics: list[str], languages: list[str] | None) -> list[str]:
    """Topics in turn by language, so each update gets some in every language."""
    groups: dict[str | None, list[str]] = {}
    for topic in topics:
        language = next((language for language in languages or [] if in_language(language, topic)), None)
        groups.setdefault(language, []).append(topic)
    queues = list(groups.values())
    return [queue[index] for index in range(max(map(len, queues), default=0)) for queue in queues
            if index < len(queue)]


def parse(text: str, exclude: list[str]) -> list[str]:
    data = parse_json(text)
    values = data.get("topics") if isinstance(data, dict) else None
    topics: list[str] = []
    for value in values if isinstance(values, list) else []:
        topic = " ".join(value.split()) if isinstance(value, str) else ""
        if 0 < len(topic) <= TOPIC_MAX and topic not in topics and not (exclude and matches(topic, exclude)):
            topics.append(topic)
    return topics[:COUNT]


def make_topics(llm: LLMClient, genre: dict[str, Any], used: list[str]) -> tuple[list[str], LLMResult]:
    system, user = prompts(genre, used)
    result = llm.complete(system, [{"role": "user", "content": user}], SCHEMA)
    topics = [] if result.refused else parse(result.text, list(genre.get("exclude_terms") or []))
    return mixed(topics, languages_of(list(genre.get("locales") or ["all"]))), result

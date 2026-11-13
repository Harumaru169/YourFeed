from datetime import timedelta

import httpx
import pytest

from yourfeed.search.plan import SITE_QUERIES, TAKE, GenreSearch, Site, Window, queries, terms_for
from yourfeed.search.searxng import Result, SearchError, Searxng, canonical_url, parse_results


def entry(url, title="題名", **extra):
    return {"url": url, "title": title, "content": "説明", "template": "default.html", **extra}


def test_parse_results_keeps_readable_pages():
    results = parse_results({"results": [
        entry("https://news.example/a?utm_source=x&id=1#top", publishedDate="2026-11-05T01:00:00"),
        entry("https://img.example/photo.jpg"),
        entry("https://www.youtube.com/watch?v=1"),
        entry("https://paper.example/p", template="paper.html", publishedDate="2026-11-04T09:00:00+09:00"),
        entry("https://img.example/x", template="images.html"),
        entry("ftp://files.example/a"),
        entry("https://untitled.example/a", title="  "),
        entry("https://home.example/"),
    ]})
    assert [result.url for result in results] == ["https://news.example/a?id=1", "https://paper.example/p"]
    assert results[0].published_at.isoformat() == "2026-11-05T01:00:00+00:00"
    assert results[1].published_at.utcoffset().total_seconds() == 9 * 3600
    with pytest.raises(SearchError):
        parse_results({"error": "x"})


def test_canonical_url_rejects_credentials_and_other_schemes():
    assert canonical_url("https://User@Example.com/a") is None
    assert canonical_url("javascript:alert(1)") is None
    assert canonical_url("https://Example.com") == "https://example.com/"


def test_searxng_sends_json_queries_and_maps_errors():
    seen = []

    def handler(request):
        seen.append(dict(request.url.params))
        if request.url.params["q"] == "blocked":
            return httpx.Response(403)
        return httpx.Response(200, json={"results": [entry("https://a.example/1")]})

    searxng = Searxng("http://searxng:8080/", httpx.Client(transport=httpx.MockTransport(handler)), min_interval=0)
    assert [result.url for result in searxng.search("量子", page=2, categories="news", time_range="week",
                                                    language="ja-JP")] == ["https://a.example/1"]
    assert seen[0] == {"q": "量子", "format": "json", "categories": "news", "pageno": "2", "time_range": "week",
                       "language": "ja-JP"}
    with pytest.raises(SearchError) as error:
        searxng.search("blocked")
    assert (error.value.code, error.value.fatal) == ("search_format", True)

    def down(request):
        raise httpx.ConnectError("refused")
    with pytest.raises(SearchError) as error:
        Searxng("http://x", httpx.Client(transport=httpx.MockTransport(down)), min_interval=0).search("q")
    assert (error.value.code, error.value.fatal) == ("search_unavailable", True)


def test_sites_match_the_host_its_subdomains_and_the_path():
    site = Site.parse("https://example.com/tech/")
    assert site.operator == "site:example.com/tech"
    assert site.contains("https://www.example.com/tech/a")
    assert not site.contains("https://example.com/technology")
    assert not site.contains("https://notexample.com/tech/a")
    assert Site.parse("https://example.com").contains("https://example.com/")


def test_queries_use_the_genre_name_and_exclusions():
    assert queries("宇宙", [], ["広告"]) == ([], ["宇宙 -広告"])
    assert queries("AI", ["評価", "agent eval"], ["press release"]) == (
        ['AI 評価 -"press release"', 'AI agent eval -"press release"'], ['AI -"press release"'])


def test_sites_come_first_then_focus_and_discovery_by_share():
    plan = GenreSearch({"name": "AI", "sites": ["https://a.example"], "focus_terms": ["評価"],
                        "exclude_terms": [], "discovery_percent": 20})
    window = plan.windows[0]
    stream, limit = plan.next_stream(window, 10)
    assert (stream.query, limit) == ("AI 評価 site:a.example", TAKE)
    stream.done = True
    taken = {"AI 評価": 0, "AI": 0}
    while sum(taken.values()) < 10:
        stream, limit = plan.next_stream(window, 10 - sum(taken.values()))
        assert limit <= TAKE
        plan.taken(stream, limit)
        taken[stream.query] += limit
    assert taken == {"AI 評価": 8, "AI": 2}
    plan.streams(window)[1][0].done = True
    stream, limit = plan.next_stream(window, 8)
    assert (stream.query, limit) == ("AI", TAKE)  # the focus query ran out, so discovery fills the rest


def test_queries_take_turns():
    plan = GenreSearch({"name": "AI", "sites": [], "focus_terms": ["a", "b", "c"], "exclude_terms": [],
                        "discovery_percent": 0})
    order = []
    for _ in range(6):
        stream, limit = plan.next_stream(plan.windows[0], 20)
        plan.taken(stream, limit)
        order.append(stream.query)
    assert order == ["AI a", "AI b", "AI c"] * 2


def test_without_searches_only_waiting_results_are_used():
    plan = GenreSearch({"name": "AI", "sites": [], "focus_terms": ["a", "b"], "exclude_terms": [],
                        "discovery_percent": 0})
    window = plan.windows[0]
    plan.streams(window)[1][1].pending.append(Result("https://b.example/1", "b"))
    assert plan.next_stream(window, 5)[0].query == "AI a"
    assert plan.next_stream(window, 5, can_search=False)[0].query == "AI b"
    plan.streams(window)[1][1].pending.clear()
    assert plan.next_stream(window, 5, can_search=False) is None


def test_without_terms_the_genre_name_is_searched():
    plan = GenreSearch({"name": "宇宙", "sites": [], "focus_terms": [], "exclude_terms": [],
                        "discovery_percent": 20})
    stream, limit = plan.next_stream(Window("general"), 50)
    assert (stream.query, limit) == ("宇宙", TAKE)


def test_a_stream_never_gets_more_than_the_remaining_need():
    plan = GenreSearch({"name": "AI", "sites": [], "focus_terms": ["評価"], "exclude_terms": [],
                        "discovery_percent": 20})
    window = plan.windows[0]
    focus = plan.streams(window)[1][0]
    plan.taken(focus, 40)  # earlier windows found only focus results
    stream, limit = plan.next_stream(plan.windows[1], 1)
    assert stream.discovery and limit == 1


def test_latest_genres_search_the_past_week_only():
    week = timedelta(days=7)
    assert GenreSearch({"name": "AI"}).windows == (Window("news", None, week), Window("general", "week", week),
                                                   Window("science", None, week))


def test_site_searches_are_limited_and_take_turns():
    genre = {"name": "AI", "locales": ["ja-JP", "en"], "sites": [f"https://s{n}.example" for n in range(4)],
             "focus_terms": ["a", "b", "c"], "exclude_terms": [], "discovery_percent": 20}
    first, second = GenreSearch(genre, offset=0), GenreSearch(genre, offset=1)
    queries = lambda plan: [query for _, _, query in plan.site_queries]  # noqa: E731
    assert len(first.site_queries) == SITE_QUERIES
    # The first term on every site in every locale comes first, then the next term.
    assert queries(first)[:8] == ["AI a"] * 8 and queries(first)[8:] == ["AI b"] * 2
    assert queries(second)[:6] == ["AI b"] * 6 and set(queries(second)[6:]) == {"AI c"}
    assert [stream.query for stream in first.streams(first.windows[0], "en")[0]][:1] == ["AI a site:s0.example"]


def test_genres_of_any_age_search_their_topics_without_a_time_limit():
    genre = {"name": "思想", "latest": False, "sites": ["https://a.example"], "focus_terms": ["禅"],
             "exclude_terms": ["広告"], "discovery_percent": 20, "search_topics": ["ストア派 倫理", "禅 修行"]}
    plan = GenreSearch(genre)
    assert plan.windows[0] == Window("general")
    stream, _ = plan.next_stream(plan.windows[0], 10)
    assert stream.query == "ストア派 倫理 -広告 site:a.example"
    plan.streams(plan.windows[0])[0][0].done = plan.streams(plan.windows[0])[0][1].done = True
    stream, limit = plan.next_stream(plan.windows[0], 10)
    assert (stream.query, stream.discovery, limit) == ("ストア派 倫理 -広告", True, TAKE)
    plan.taken(stream, limit)
    assert plan.next_stream(plan.windows[0], 8)[0].query == "禅 修行 -広告"
    without_topics = GenreSearch({**genre, "sites": [], "search_topics": []})
    assert without_topics.next_stream(Window("general"), 5)[0].query == "思想 -広告"


def test_each_locale_gets_an_even_share():
    plan = GenreSearch({"name": "AI", "locales": ["ja-JP", "en-US"], "sites": [], "focus_terms": [],
                        "exclude_terms": [], "discovery_percent": 20})
    window = plan.windows[0]
    stream, limit = plan.next_stream(window, 10)
    assert (stream.query, stream.locale, limit) == ("AI", "ja-JP", TAKE)
    plan.taken(stream, 2)
    stream, limit = plan.next_stream(window, 8)
    assert (stream.locale, limit) == ("en-US", TAKE)
    plan.taken(stream, 1)
    assert plan.next_stream(window, 7)[0].locale == "en-US"
    plan.streams(window, "en-US")[2][0].done = True
    assert plan.next_stream(window, 7)[0].locale == "ja-JP"


def test_topics_are_searched_in_their_own_language():
    plan = GenreSearch({"name": "哲学", "latest": False, "locales": ["ja-JP", "en"], "sites": [],
                        "focus_terms": [], "exclude_terms": [], "discovery_percent": 20,
                        "search_topics": ["ストア派の倫理", "stoic ethics"]})
    window = plan.windows[0]
    assert [stream.query for stream in plan.streams(window, "ja-JP")[2]] == ["ストア派の倫理"]
    assert [stream.query for stream in plan.streams(window, "en")[2]] == ["stoic ethics"]


def test_each_locale_searches_its_translated_terms():
    genre = {"name": "宇宙", "locales": ["ja-JP", "en-US"], "sites": [], "focus_terms": ["探査機"],
             "exclude_terms": ["広告"], "discovery_percent": 20,
             "translations": {"en": {"name": "space", "focus_terms": ["probe"], "exclude_terms": ["ads"]}}}
    assert terms_for(genre, "en-US") == ("space", ["probe"], ["ads"])
    assert terms_for(genre, "ja-JP") == ("宇宙", ["探査機"], ["広告"])
    plan = GenreSearch(genre)
    assert plan.queries["en-US"] == (["space probe -ads"], ["space -ads"])
    assert plan.excludes["en-US"] == ["広告", "ads"]

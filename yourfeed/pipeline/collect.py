"""Search the web for new candidates, split by genre weight."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Protocol
from urllib.parse import urlsplit

from sqlalchemy import text
from sqlalchemy.engine import Engine

from yourfeed.search.plan import MAX_PAGES, GenreSearch, Stream, Window
from yourfeed.search.searxng import Result, SearchError

from .pagetext import RETRYABLE
from .select import allocate, fill_by_weight, matches

CANDIDATES = 50
MAX_SEARCHES = 80
MAX_SEARCH_ERRORS = 3
# A host whose pages failed this often for these reasons, and never read, is not used.
UNREADABLE = ("too_little_text", "robots_disallow", "unreadable", "http_401", "http_402", "http_403")
UNREADABLE_AFTER = 3


class Searcher(Protocol):
    """What the collector needs from a search backend."""

    def search(self, query: str, *, page: int = 1, categories: str = "general",
               time_range: str | None = None, language: str = "all") -> list[Result]: ...


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def unreadable_hosts(engine: Engine) -> set[str]:
    """Hosts whose pages failed UNREADABLE_AFTER times and were never read. Temporary failures count as neither."""
    with engine.connect() as connection:
        rows = connection.execute(text("""
            SELECT url, skip_reason FROM items
            WHERE attempted_at IS NOT NULL AND (skip_reason IS NULL OR NOT skip_reason = ANY(:temporary))
        """), {"temporary": sorted(RETRYABLE)}).all()
    failed: dict[str, int] = {}
    read: set[str] = set()
    for url, reason in rows:
        host = host_of(url)
        if reason in UNREADABLE:
            failed[host] = failed.get(host, 0) + 1
        else:
            read.add(host)
    return {host for host, count in failed.items() if count >= UNREADABLE_AFTER and host not in read}


class Collector:
    def __init__(self, engine: Engine, searcher: Searcher, run_id: int, now: datetime | None = None) -> None:
        self.engine, self.searcher, self.run_id = engine, searcher, run_id
        self.now = now or datetime.now(timezone.utc)
        self.searches = 0
        self.errors = 0
        self.error: str | None = None
        self.stopped = False
        self.rank = 0
        self.found: dict[Any, int] = {}
        self.used: dict[Any, int] = {}
        self.caps: dict[Any, int] | None = None
        self.skip_hosts: set[str] = set()
        self._plans: dict[Any, GenreSearch] = {}

    def collect(self, genres: list[dict], total: int = CANDIDATES) -> dict[Any, int]:
        """Search until `total` new URLs are stored; returns each genre's target. Each genre first
        gets its weight's share of the searches, and what is left goes to the genres still short."""
        for index, genre in enumerate(genres):
            self._plans[genre["id"]] = GenreSearch(genre, offset=self.run_id + index)
            self.found[genre["id"]] = self.used[genre["id"]] = 0
        self.skip_hosts = unreadable_hosts(self.engine)
        weights = {genre["id"]: genre["weight"] for genre in genres}
        stages = range(max((len(plan.windows) for plan in self._plans.values()), default=0))
        count = lambda genre_id: self.found[genre_id]  # noqa: E731
        self.caps = allocate(weights, MAX_SEARCHES)
        targets = fill_by_weight(weights, total, stages, count, self._fill)
        if not self.stopped and self.searches < MAX_SEARCHES and sum(self.found.values()) < total:
            self.caps = None
            targets = fill_by_weight(weights, total, stages, count, self._fill)
        return targets

    def _can_search(self, genre_id: Any) -> bool:
        return (not self.stopped and self.searches < MAX_SEARCHES
                and (self.caps is None or self.used[genre_id] < self.caps.get(genre_id, 0)))

    def _fill(self, genre_id: Any, target: int, stage: int) -> bool:
        """Search the genre's `stage`-th window until `target`; False when it has nothing more."""
        plan = self._plans[genre_id]
        if stage >= len(plan.windows):
            return False
        window = plan.windows[stage]
        while self.found[genre_id] < target:
            picked = plan.next_stream(window, target - self.found[genre_id], self._can_search(genre_id))
            if picked is None:
                return False
            stream, limit = picked
            added = self._take(genre_id, stream, window, limit)
            plan.taken(stream, added)
            self.found[genre_id] += added
        return True

    def _take(self, genre_id: Any, stream: Stream, window: Window, limit: int) -> int:
        """Store up to `limit` new URLs from the stream, searching its next page when none are waiting."""
        added = 0
        while added < limit:
            if not stream.pending:
                if stream.done or not self._can_search(genre_id):
                    break
                self._search(genre_id, stream, window)
                continue
            result = stream.pending.popleft()
            self.rank += 1
            with self.engine.begin() as connection:
                added += connection.execute(text("""
                    INSERT INTO items (genre_id, url, title, snippet, published_at, run_id, rank)
                    VALUES (:genre_id, :url, :title, :snippet, :published_at, :run_id, :rank)
                    ON CONFLICT (url) DO NOTHING
                """), {"genre_id": genre_id, "url": result.url, "title": result.title,
                       "snippet": result.snippet or None, "published_at": result.published_at,
                       "run_id": self.run_id, "rank": self.rank}).rowcount
        return added

    def _search(self, genre_id: Any, stream: Stream, window: Window) -> None:
        self.searches += 1
        self.used[genre_id] += 1
        try:
            results = self.searcher.search(stream.query, page=stream.page, categories=window.categories,
                                           time_range=window.time_range, language=stream.locale)
        except SearchError as exc:
            self.error = exc.code
            self.errors += 1
            stream.done = True
            if exc.fatal or self.errors >= MAX_SEARCH_ERRORS:
                self.stopped = True
            return
        self.errors = 0
        stream.page += 1
        fresh = [result for result in results if result.url not in stream.seen]
        stream.seen.update(result.url for result in results)
        if not fresh or stream.page > MAX_PAGES:
            stream.done = True  # no more pages, or the engine repeats the same page
        exclude = self._plans[genre_id].excludes.get(stream.locale, [])
        oldest = self.now - window.max_age if window.max_age else None
        for result in fresh:
            if stream.site is not None and not stream.site.contains(result.url):
                continue
            if exclude and matches(f"{result.title} {result.snippet}", exclude):
                continue
            if oldest and result.published_at and result.published_at < oldest:
                continue
            if host_of(result.url) in self.skip_hosts:
                continue
            stream.pending.append(result)

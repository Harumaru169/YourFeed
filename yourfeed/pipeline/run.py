"""One update: search for new candidates, summarize them and post the set count by genre weight."""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any, NamedTuple
from urllib.parse import urlsplit

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from yourfeed import config
from yourfeed.db import WORKER_LOCK
from yourfeed.llm import LLMClient, LLMError, make_client
from yourfeed.search.language import LENGTH_SCALE, languages_of, page_language
from yourfeed.search.plan import LATEST_AGE
from yourfeed.search.searxng import Searxng
from yourfeed.fetch import SafeHttpClient

from .collect import CANDIDATES, MAX_SEARCHES, Collector, Searcher
from .concurrency import LimitedLLM, LimitedSearch, StaleUpdate, transaction
from .pagetext import RETRYABLE, Page, PageFetcher
from .prompt import TITLE_MAX, Style
from .select import allocate, by_site
from .summarize import summarize
from .topics import make_topics, per_update
from .translate import missing, translate_terms

class PostWindow(NamedTuple):
    """Pages a post may come from: dated within `max_age` (None: any age), undated ones only if `undated`."""
    max_age: timedelta | None
    undated: bool = True


# Tried in order until the count is filled: genres of the latest news take dated pages of the past
# week first, then undated ones; genres of any age take any page.
LATEST_WINDOWS = (PostWindow(LATEST_AGE, undated=False), PostWindow(LATEST_AGE))
ANY_AGE_WINDOWS = (PostWindow(None),)
RETRY_AFTER = timedelta(hours=3)
# How long old data is kept. A URL that is forgotten may be found again.
KEEP_CANDIDATES = timedelta(days=3)  # found but not used yet
KEEP_FAILED = timedelta(days=14)  # pages that could not be used; the recent ones decide unreadable sites
KEEP_POSTED = timedelta(days=30)  # posted URLs, so the same page is not posted twice
KEEP_RUNS = timedelta(days=7)
MAX_CONSECUTIVE_ERRORS = 3
CALLS_PER_POST = 8


def candidates_for(items_per_run: int) -> int:
    """New URLs to search for: half again the posts, since some pages cannot be used; at least CANDIDATES."""
    return max(CANDIDATES, -(-items_per_run * 3 // 2))


def load_candidates(connection: Connection, genre_id: Any, now: datetime, window: timedelta | None,
                    dated_first: bool = True) -> list[dict[str, Any]]:
    """Unposted candidates: latest search first, dated before undated if asked, then in search order."""
    order = "i.published_at IS NULL, i.rank" if dated_first else "i.rank"
    return [dict(row) for row in connection.execute(text(f"""
        SELECT i.id, i.title, i.url, i.published_at, (sm.item_id IS NOT NULL) AS has_summary
        FROM items i
        LEFT JOIN summaries sm ON sm.item_id = i.id
        WHERE i.genre_id = :genre_id AND i.posted_at IS NULL
          AND COALESCE(i.published_at, i.found_at) <= :latest
          AND (CAST(:since AS timestamptz) IS NULL OR COALESCE(i.published_at, i.found_at) >= :since)
          AND (sm.item_id IS NOT NULL OR i.attempted_at IS NULL
               OR (i.skip_reason = ANY(:retryable) AND i.attempted_at < :retry_before))
        ORDER BY i.run_id DESC NULLS LAST, {order}
    """), {"genre_id": genre_id, "since": now - window if window else None,
           "latest": now + timedelta(days=1), "retryable": sorted(RETRYABLE),
           "retry_before": now - RETRY_AFTER}).mappings()]


Backend = tuple[str, "LLMClient | LLMError"]  # a provider, and its client or why it could not be set up


def make_clients(settings: dict[str, Any]) -> list[Backend]:
    """The main provider, then the fallbacks, in the order they are tried."""
    backends = [{"provider": settings["llm_provider"], "model": settings["llm_model"]},
                *(settings.get("llm_fallbacks") or [])]
    clients: list[Backend] = []
    for backend in backends:
        try:
            clients.append((backend["provider"],
                            make_client({"llm_provider": backend["provider"], "llm_model": backend["model"]})))
        except LLMError as exc:
            clients.append((backend["provider"], exc))
    return clients


class _Posting:
    def __init__(self, engine: Engine, http: SafeHttpClient, clients: list[Backend], now: datetime,
                 run_id: int, call_cap: int, genres: dict[Any, dict[str, Any]]) -> None:
        self.engine, self.now = engine, now
        self.run_id, self.call_cap, self.genres = run_id, call_cap, genres
        self.generation = None
        self.quota = None
        self.posted = 0
        self.queues: dict[tuple[Any, int], deque[dict[str, Any]]] = {}
        self.fetcher = PageFetcher(http)
        self.calls = self.input_tokens = self.output_tokens = 0
        self.consecutive_errors = 0
        self.error: str | None = None
        self.tried: set[Any] = set()
        self.pages: dict[Any, Page] = {}
        self.accepted: dict[Any, list[Any]] = {genre_id: [] for genre_id in genres}
        self.clients = clients
        self.llm: LLMClient | None = None
        self.switches: list[dict[str, str | None]] = []  # each provider given up on, why, and the next one
        self._use(0)

    def _use(self, index: int, failed: tuple[str, str] | None = None) -> None:
        """Use the first client from `index` on that could be set up, noting each provider passed over."""
        given_up = [failed] if failed else []
        while index < len(self.clients) and isinstance(self.clients[index][1], LLMError):
            provider, error = self.clients[index]
            given_up.append((provider, error.code))
            self.error = error.code
            index += 1
        self.index = index
        self.llm = self.clients[index][1] if index < len(self.clients) else None  # type: ignore[assignment]
        for position, (provider, code) in enumerate(given_up):
            following = given_up[position + 1][0] if position + 1 < len(given_up) else \
                (self.clients[index][0] if index < len(self.clients) else None)
            self.switches.append({"from": provider, "error": code, "to": following})

    def _failed(self, exc: LLMError) -> bool:
        """Count a failed call. A provider that cannot be used hands over to the next; True if it did."""
        self.error = exc.code
        self.consecutive_errors += 1
        if not (exc.fatal or exc.code == "http_429" or self.consecutive_errors >= MAX_CONSECUTIVE_ERRORS):
            return False
        self.consecutive_errors = 0
        self._use(self.index + 1, failed=(self.clients[self.index][0], exc.code))
        return True

    def _mark_attempted(self, item_id: Any, reason: str) -> None:
        with transaction(self.engine, self.generation) as connection:
            connection.execute(text("UPDATE items SET attempted_at = now(), skip_reason = :reason WHERE id = :id"),
                               {"id": item_id, "reason": reason})

    def _count(self, calls: int, input_tokens: int, output_tokens: int) -> None:
        self.calls += calls
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        with transaction(self.engine, self.generation) as connection:
            connection.execute(text("""
                UPDATE runs SET llm_calls = llm_calls + :calls, input_tokens = input_tokens + :input, output_tokens = output_tokens + :output WHERE id = :id
            """), {"id": self.run_id, "calls": calls, "input": input_tokens, "output": output_tokens})

    def _summarize(self, genre: dict[str, Any], row: dict[str, Any], window: PostWindow) -> str:
        """'ok', 'later' when the page is undated and the window wants dated ones, or 'no'."""
        since = self.now - window.max_age if window.max_age else None
        page = self.pages.get(row["id"])
        if page is None:
            page, reason = self.fetcher.read(row["url"])
            if page is None:
                self._mark_attempted(row["id"], reason or "too_little_text")
                return "no"
            self.pages[row["id"]] = page
        language = page_language(languages_of(genre["locales"]), f"{page.title or ''}\n{page.text}", page.language)
        if language is None:
            self._mark_attempted(row["id"], "other_language")
            return "no"
        # A page's own date is more exact than the search result's, except a PDF's file date.
        page_date = None if page.is_pdf and row["published_at"] else page.published_at
        published_at = page_date or row["published_at"]
        if page_date and page_date != row["published_at"]:
            with transaction(self.engine, self.generation) as connection:
                connection.execute(text("UPDATE items SET published_at = :at WHERE id = :id"),
                                   {"id": row["id"], "at": page_date})
        if since and published_at and published_at < since:
            self._mark_attempted(row["id"], "too_old")
            return "no"
        if published_at is None and not window.undated:
            return "later"  # undated pages wait until the dated ones are used
        # Untranslated summaries are written in the page's language, with lengths scaled to it.
        written = None if genre["translate"] or language == "ja" else language
        scale = LENGTH_SCALE.get(written or "", 1.0)
        style = Style(round(genre["min_chars"] * scale), round(genre["max_chars"] * scale), genre["stance"],
                      tuple(genre["focus_terms"]), genre["name"], genre["latest"], written, round(TITLE_MAX * scale))
        title = page.title or row["title"]
        site_name = page.site_name or (urlsplit(row["url"]).hostname or "").removeprefix("www.")
        try:
            outcome = summarize(self.llm, style, source_name=site_name, title=title,
                                published_at=published_at, body=page.text)
        except LLMError as exc:
            # The page is tried again with the next provider when this one cannot be used.
            return "retry" if self._failed(exc) else "no"
        self.consecutive_errors = 0
        self._count(outcome.calls, outcome.input_tokens, outcome.output_tokens)
        if outcome.status != "ok" or outcome.summary is None:
            self._mark_attempted(row["id"], outcome.status)
            return "no"
        with transaction(self.engine, self.generation) as connection:
            connection.execute(text("""
                INSERT INTO summaries (item_id, title, facts, interpretation, provider, model)
                VALUES (:id, :title, :facts, :interpretation, :provider, :model)
                ON CONFLICT (item_id) DO NOTHING
            """), {"id": row["id"], "title": outcome.summary.title, "facts": outcome.summary.facts,
                   "interpretation": outcome.summary.interpretation,
                   "provider": self.llm.provider, "model": self.llm.model})
            connection.execute(text("""
                UPDATE items SET attempted_at = now(), skip_reason = NULL, title = :title, site_name = :site
                WHERE id = :id
            """), {"id": row["id"], "title": title, "site": site_name})
        return "ok"

    def _queue(self, genre: dict[str, Any], stage: int, window: PostWindow) -> deque[dict[str, Any]]:
        key = (genre["id"], stage)
        if key not in self.queues:
            with self.engine.connect() as connection:
                rows = {row["id"]: row for row in load_candidates(connection, genre["id"], self.now, window.max_age,
                                                                  genre["latest"]) if row["id"] not in self.tried}
            self.queues[key] = deque(rows[item_id] for item_id in by_site([(row["id"], row["url"])
                                                                           for row in rows.values()]))
        return self.queues[key]

    def fill(self, genre_id: Any, target: int, stage: int) -> bool:
        """Post items from the genre's `stage`-th window as they are summarized, until `target`;
        False when nothing is left."""
        genre = self.genres[genre_id]
        windows = LATEST_WINDOWS if genre["latest"] else ANY_AGE_WINDOWS
        if stage >= len(windows):
            return False
        window = windows[stage]
        queue = self._queue(genre, stage, window)
        accepted = self.accepted[genre_id]
        while len(accepted) < target and queue:
            row = queue.popleft()
            if row["id"] in self.tried:
                continue
            if row["has_summary"]:
                result = "ok"
            elif self.llm is not None and self.calls < self.call_cap:
                result = self._summarize(genre, row, window)
            else:
                continue
            if result == "retry":
                queue.appendleft(row)
                continue
            if result != "later":  # an undated page is tried again in the next window
                self.tried.add(row["id"])
            if result == "ok":
                accepted.append(row["id"])
                self.posted += publish(self.engine, [(genre_id, row["id"])], generation=self.generation, quota=self.quota)
        return len(accepted) >= target

    def search_terms(self, genre: dict[str, Any]) -> dict[str, Any]:
        """The genre's name and terms in each chosen language; the model translates the missing ones."""
        translations = dict(genre["translations"] or {})
        languages = missing(genre)
        made: dict[str, dict[str, Any]] = {}
        while languages and self.llm is not None:
            try:
                made, result = translate_terms(self.llm, genre, languages)
            except LLMError as exc:
                if self._failed(exc):
                    continue
                return translations
            self.consecutive_errors = 0
            self._count(1, result.input_tokens, result.output_tokens)
            break
        if made:
            translations.update(made)
            with transaction(self.engine, self.generation) as connection:
                connection.execute(text("UPDATE genres SET translations = CAST(:translations AS jsonb) WHERE id = :id"),
                                   {"id": genre["id"], "translations": json.dumps(translations, ensure_ascii=False)})
        return translations

    def search_topics(self, genre: dict[str, Any], count: int) -> list[str]:
        """This update's `count` topics for a genre of any age; the model makes new ones when they run out."""
        if genre["latest"]:
            return []
        topics, index = list(genre["topics"]), genre["topic_index"]
        made: list[str] = []
        while index + count > len(topics) and self.llm is not None:
            try:
                made, result = make_topics(self.llm, genre, topics)
            except LLMError as exc:
                if self._failed(exc):
                    continue
                break
            self.consecutive_errors = 0
            self._count(1, result.input_tokens, result.output_tokens)
            break
        if made:
            topics, index = made, 0
        picked = topics[index:index + count]
        with transaction(self.engine, self.generation) as connection:
            connection.execute(text("UPDATE genres SET topics = :topics, topic_index = :index WHERE id = :id"),
                               {"id": genre["id"], "topics": topics, "index": index + len(picked)})
        return picked


CLOSE_INTERRUPTED = """
    UPDATE runs SET status = 'error', error = 'interrupted', finished_at = now() WHERE finished_at IS NULL
"""


def close_interrupted(engine: Engine) -> None:
    """Close runs that were stopped midway, unless an update is running now."""
    with engine.connect() as lock:
        if not lock.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": WORKER_LOCK}).scalar_one():
            return
        try:
            with engine.begin() as connection:
                connection.execute(text(CLOSE_INTERRUPTED))
        finally:
            lock.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": WORKER_LOCK})


def run_cycle(engine: Engine, *, http: SafeHttpClient | None = None,
              llm: LLMClient | list[LLMClient] | None = None, searcher: Searcher | None = None,
              now: datetime | None = None) -> dict[str, Any]:
    """One update. `llm` replaces the configured providers: one client, or several tried in order."""
    with engine.connect() as lock:
        if not lock.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": WORKER_LOCK}).scalar_one():
            return {"status": "busy"}
        try:
            return _cycle(engine, http, llm, searcher, now or datetime.now(timezone.utc))
        finally:
            lock.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": WORKER_LOCK})


def _cycle(engine: Engine, http: SafeHttpClient | None, llm: LLMClient | list[LLMClient] | None,
           searcher: Searcher | None, now: datetime) -> dict[str, Any]:
    with engine.begin() as connection:
        connection.execute(text(CLOSE_INTERRUPTED))
        settings = dict(connection.execute(text("SELECT * FROM settings WHERE id = 1 FOR UPDATE")).mappings().one())
        genres = [dict(row) for row in connection.execute(text("SELECT * FROM genres WHERE weight > 0 ORDER BY position, created_at, id")).mappings()]
        total = settings["feed_size"]
        targets = allocate({g["id"]: g["weight"] for g in genres}, total)
        candidate_targets = allocate({g["id"]: targets[g["id"]] for g in genres}, candidates_for(total))
        search_limits = allocate(targets, MAX_SEARCHES)
        run_id = connection.execute(text("INSERT INTO runs (target, found_target) VALUES (:target, :found) RETURNING id"),
                                    {"target": total, "found": sum(candidate_targets.values())}).scalar_one()
    own_http, own_searcher = http is None, searcher is None
    http = http or SafeHttpClient()
    searcher = searcher or Searxng(config.searxng_url())
    limited_search = LimitedSearch(searcher, settings["search_concurrency"])
    llm_limit = threading.BoundedSemaphore(settings["llm_concurrency"])
    fetcher = PageFetcher(http)
    detail: dict[str, Any] = {"genres": {}, "switches": []}
    results = []

    def run_genre(genre):
        genre_id = genre["id"]
        target = targets[genre_id]
        given = [llm] if llm is not None and not isinstance(llm, list) else llm
        clients = [(client.provider, client) for client in given] if given is not None else make_clients(settings)
        clients = [(name, client if isinstance(client, LLMError) else LimitedLLM(client, llm_limit)) for name, client in clients]
        posting = _Posting(engine, http, clients, now, run_id, target * CALLS_PER_POST, {genre_id: genre})
        posting.fetcher, posting.generation, posting.quota = fetcher, settings["generation"], target
        collector = Collector(engine, limited_search, run_id, now, settings["generation"], search_limits[genre_id])
        try:
            genre["translations"] = posting.search_terms(genre)
            genre["search_topics"] = posting.search_topics(genre, per_update(candidate_targets[genre_id]))
            collector.collect([genre], candidate_targets[genre_id])
            for stage in range(len(LATEST_WINDOWS)):
                if posting.fill(genre_id, target, stage):
                    break
            error = posting.error or collector.error
        except StaleUpdate:
            error = "settings_changed"
        except Exception as exc:
            error = type(exc).__name__
        finally:
            if given is None:
                for _, wrapper in clients:
                    if isinstance(wrapper, LimitedLLM):
                        resource = getattr(wrapper.client, "client", None)
                        if resource is not None and callable(getattr(resource, "close", None)):
                            resource.close()
        return {"id": str(genre_id), "target": target, "posted": posting.posted,
                "found": collector.found.get(genre_id, 0), "found_target": candidate_targets[genre_id],
                "searches": collector.searches, "search_error": collector.error, "error": error,
                "calls": posting.calls, "input": posting.input_tokens, "output": posting.output_tokens,
                "switches": posting.switches}

    try:
        forget_old(engine, now)
        active = [g for g in genres if targets[g["id"]] > 0]
        workers = min(len(active), max(5, settings["search_concurrency"], settings["llm_concurrency"]))
        if workers:
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="genre") as pool:
                futures = {pool.submit(run_genre, genre): genre for genre in active}
                for future in as_completed(futures):
                    try:
                        result = future.result()
                    except Exception as exc:
                        genre = futures[future]
                        result = {"id": str(genre["id"]), "target": targets[genre["id"]], "posted": 0,
                                  "found": 0, "found_target": candidate_targets[genre["id"]], "searches": 0,
                                  "search_error": None, "error": type(exc).__name__, "calls": 0,
                                  "input": 0, "output": 0, "switches": []}
                    results.append(result)
                    detail["genres"][result["id"]] = result
                    detail["switches"].extend(result["switches"])
                    with engine.begin() as connection:
                        connection.execute(text("UPDATE runs SET posted = :posted, found = :found, searches = :searches, detail = CAST(:detail AS jsonb) WHERE id = :id"),
                            {"id": run_id, "posted": sum(r["posted"] for r in results), "found": sum(r["found"] for r in results),
                             "searches": sum(r["searches"] for r in results), "detail": json.dumps(detail)})
    except Exception as exc:
        with engine.begin() as connection:
            connection.execute(text("UPDATE runs SET status = 'error', error = :error, finished_at = now() WHERE id = :id"),
                               {"id": run_id, "error": type(exc).__name__})
        raise
    finally:
        if own_http:
            http.close()
        if own_searcher:
            searcher.close()
    posted, found = sum(r["posted"] for r in results), sum(r["found"] for r in results)
    search_error = next((r["search_error"] for r in results if r["search_error"]), None)
    error = next((r["error"] for r in results if r["error"]), None)
    status = "partial" if posted < total or error else "ok"
    with engine.begin() as connection:
        connection.execute(text("UPDATE runs SET finished_at = now(), status = :status, posted = :posted, found = :found, searches = :searches, search_error = :search_error, error = :error, detail = CAST(:detail AS jsonb) WHERE id = :id"),
                           {"id": run_id, "status": status, "posted": posted, "found": found,
                            "searches": sum(r["searches"] for r in results), "search_error": search_error,
                            "error": error, "detail": json.dumps(detail)})
    return {"status": status, "target": total, "posted": posted, "found": found,
            "searches": sum(r["searches"] for r in results), "search_error": search_error,
            "llm_calls": sum(r["calls"] for r in results), "error": error, **detail}


def publish(engine: Engine, posts: list[tuple[Any, Any]], *,
            generation: int | None = None, quota: int | None = None) -> int:
    """Atomically add each completed card and evict the oldest excess card in its genre."""
    posted = 0
    with transaction(engine, generation) as connection:
        for genre_id, item_id in posts:
            inserted = connection.execute(text("""
                INSERT INTO timeline_entries (item_id, genre_id, title, facts, interpretation)
                SELECT item_id, :genre_id, title, facts, interpretation FROM summaries WHERE item_id = :item_id
                ON CONFLICT (item_id) DO NOTHING RETURNING id
            """), {"item_id": item_id, "genre_id": genre_id}).scalar_one_or_none()
            if inserted is None:
                continue
            posted += 1
            connection.execute(text("UPDATE items SET posted_at = now() WHERE id = :id"), {"id": item_id})
            if quota is not None:
                connection.execute(text("""
                    DELETE FROM timeline_entries WHERE id IN (
                        SELECT id FROM timeline_entries WHERE genre_id = :genre
                        ORDER BY added_at DESC, id DESC OFFSET :quota)
                """), {"genre": genre_id, "quota": quota})
    return posted


def forget_old(engine: Engine, now: datetime) -> None:
    """Drop data past its keeping time. Posts still on the timeline stay, and so does the last run."""
    with engine.begin() as connection:
        connection.execute(text("""
            DELETE FROM summaries sm USING items i
            WHERE sm.item_id = i.id AND i.posted_at IS NOT NULL
              AND NOT EXISTS (SELECT 1 FROM timeline_entries e WHERE e.item_id = i.id)
        """))
        connection.execute(text("""
            DELETE FROM items i
            WHERE NOT EXISTS (SELECT 1 FROM timeline_entries e WHERE e.item_id = i.id) AND CASE
              WHEN i.posted_at IS NOT NULL THEN i.posted_at < :posted
              WHEN i.skip_reason IS NOT NULL AND NOT i.skip_reason = ANY(:retryable)
                THEN COALESCE(i.attempted_at, i.found_at) < :failed
              ELSE i.found_at < :candidates END
        """), {"posted": now - KEEP_POSTED, "failed": now - KEEP_FAILED, "candidates": now - KEEP_CANDIDATES,
               "retryable": sorted(RETRYABLE)})
        connection.execute(text("""
            DELETE FROM runs WHERE started_at < :cutoff AND id < (SELECT max(id) FROM runs)
        """), {"cutoff": now - KEEP_RUNS})

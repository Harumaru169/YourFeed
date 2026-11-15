from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, Lock, BoundedSemaphore

import httpx

from yourfeed.fetch import SafeHttpClient
from yourfeed.pipeline.concurrency import LimitedLLM, LimitedSearch
from yourfeed.search.searxng import Searxng


def test_search_requests_overlap():
    barrier = Barrier(2, timeout=3)
    def handler(request):
        barrier.wait()
        return httpx.Response(200, json={"results": []})
    client = Searxng("https://search.example", httpx.Client(transport=httpx.MockTransport(handler)), min_interval=0)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(client.search, str(i)) for i in range(2)]
        assert [future.result() for future in futures] == [[], []]
    client.close()


def test_distinct_hosts_fetch_in_parallel():
    barrier = Barrier(2, timeout=3)
    def handler(request):
        barrier.wait()
        return httpx.Response(200, text="ok")
    client = SafeHttpClient(httpx.Client(transport=httpx.MockTransport(handler)), resolver=lambda _: ["93.184.216.34"])
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(client.fetch, f"https://{host}.example/") for host in ("a", "b")]
        assert all(future.result().body == b"ok" for future in futures)
    client.close()


def test_llm_limit_is_shared():
    entered, release, lock = Event(), Event(), Lock()
    counts = {"active": 0, "max": 0}
    class Fake:
        provider, model = "fake", "fake"
        def complete(self):
            with lock:
                counts["active"] += 1
                counts["max"] = max(counts["max"], counts["active"])
                if counts["active"] == 2:
                    entered.set()
            assert release.wait(3)
            with lock:
                counts["active"] -= 1
    client = LimitedLLM(Fake(), BoundedSemaphore(2))
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(client.complete) for _ in range(3)]
        assert entered.wait(3)
        release.set()
        for future in futures:
            future.result()
    assert counts["max"] == 2


def test_search_concurrency_is_shared():
    entered, release, lock = Event(), Event(), Lock()
    counts = {"active": 0, "max": 0}
    class Fake:
        def search(self):
            with lock:
                counts["active"] += 1
                counts["max"] = max(counts["max"], counts["active"])
                if counts["active"] == 2:
                    entered.set()
            assert release.wait(3)
            with lock:
                counts["active"] -= 1
            return []
    search = LimitedSearch(Fake(), 2)
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(search.search) for _ in range(3)]
        assert entered.wait(3)
        release.set()
        for future in futures:
            future.result()
    assert counts["max"] == 2

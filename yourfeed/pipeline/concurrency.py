"""Shared limits for one update."""
from contextlib import contextmanager
from threading import BoundedSemaphore
from sqlalchemy import text


class StaleUpdate(Exception):
    pass


@contextmanager
def transaction(engine, generation):
    with engine.begin() as connection:
        if generation is not None:
            current = connection.execute(text("SELECT generation FROM settings WHERE id = 1 FOR UPDATE")).scalar_one()
            if current != generation:
                raise StaleUpdate()
        yield connection


class LimitedLLM:
    def __init__(self, client, semaphore):
        self.client, self.semaphore = client, semaphore
        self.provider, self.model = client.provider, client.model

    def complete(self, *args, **kwargs):
        with self.semaphore:
            return self.client.complete(*args, **kwargs)


class LimitedSearch:
    def __init__(self, searcher, limit):
        self.searcher, self.semaphore = searcher, BoundedSemaphore(limit)

    def search(self, *args, **kwargs):
        with self.semaphore:
            return self.searcher.search(*args, **kwargs)

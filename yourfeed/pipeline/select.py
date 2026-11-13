"""How many search results and posts each genre gets, and in what order to try them."""

from __future__ import annotations

import re
import unicodedata
from collections import deque
from collections.abc import Callable, Hashable, Iterable, Mapping, Sequence
from math import floor
from typing import Any, TypeVar
from urllib.parse import urlsplit

K = TypeVar("K", bound=Hashable)
W = TypeVar("W")


def normalized(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def has_term(normalized_text: str, term: str) -> bool:
    term = normalized(term.strip())
    if not term:
        return False
    if not term.isascii():
        return term in normalized_text
    return re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", normalized_text) is not None


def matches(value: str, terms: Iterable[str]) -> bool:
    text = normalized(value)
    return any(has_term(text, term) for term in terms)


def allocate(weights: Mapping[K, int], size: int) -> dict[K, int]:
    """Largest remainder; ties go to the earlier key."""
    total = sum(max(0, weight) for weight in weights.values())
    if size <= 0 or total <= 0:
        return {key: 0 for key in weights}
    exact = {key: size * max(0, weight) / total for key, weight in weights.items()}
    quota = {key: floor(value) for key, value in exact.items()}
    order = sorted(enumerate(weights), key=lambda pair: (-(exact[pair[1]] - quota[pair[1]]), pair[0]))
    for _, key in order[:size - sum(quota.values())]:
        quota[key] += 1
    return quota


def fill_by_weight(weights: Mapping[K, int], total: int, windows: Sequence[W],
                   count: Callable[[K], int], fill: Callable[[K, int, W], bool],
                   step: int | None = None, move_short: bool = True) -> dict[K, int]:
    """Split `total` by weight. Each key widens its window until its share is filled; with `move_short`,
    what is still missing then goes to the keys that have more. `fill` returns False when its window
    has nothing left. Never fills past `total`. Returns the targets.
    With `step`, the key furthest from its target goes next and adds at most `step` items."""
    keys = [key for key, weight in weights.items() if weight > 0]
    targets = {key: 0 for key in weights} | allocate({key: weights[key] for key in keys}, total)
    stage = {key: 0 for key in keys}
    filled = lambda: sum(count(key) for key in keys)  # noqa: E731
    while True:
        while filled() < total and (waiting := [key for key in keys
                                                if stage[key] < len(windows) and count(key) < targets[key]]):
            key = waiting[0] if step is None else min(waiting, key=lambda other: count(other) / targets[other])
            goal = targets[key] if step is None else min(targets[key], count(key) + step)
            if not fill(key, min(goal, count(key) + total - filled()), windows[stage[key]]):
                stage[key] += 1
        short = total - filled()
        open_keys = [key for key in keys if stage[key] < len(windows)]
        if not move_short or short <= 0 or not open_keys:
            return targets
        for key, extra in allocate({key: weights[key] for key in open_keys}, short).items():
            targets[key] += extra


def by_site(items: Sequence[tuple[Any, str]]) -> list[Any]:
    """Take (item, url) pairs one site at a time, keeping their order."""
    queues: dict[str, deque[Any]] = {}
    for item, url in items:
        queues.setdefault((urlsplit(url).hostname or "").removeprefix("www."), deque()).append(item)
    ordered: list[Any] = []
    while queues:
        for key in list(queues):
            ordered.append(queues[key].popleft())
            if not queues[key]:
                del queues[key]
    return ordered


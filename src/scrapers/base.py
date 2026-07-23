"""Scraper interface. One adapter per bookmaker; adding a bookmaker later
means adding one file that implements this and nothing else."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable, TypeVar

from ..models import Match

_T = TypeVar("_T")


def with_retries(fn: Callable[[], _T], tries: int, label: str) -> _T:
    """Run fn, retrying on any exception (browser/network can be flaky)."""
    last: Exception | None = None
    for i in range(tries):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 — scrape failures are opaque
            last = e
            print(f"[{label}] capture attempt {i + 1}/{tries} failed: "
                  f"{type(e).__name__}: {e}")
    raise last  # type: ignore[misc]


class Scraper(ABC):
    name: str

    @abstractmethod
    def fetch_games(self) -> list[Match]:
        """Return all pre-match games (for the configured sport) with 1X2 odds.

        Implementations must also dump the raw payloads they captured to
        data/raw/ before mapping — raw snapshots are the debugging ground
        truth and let us re-map without re-scraping.
        """

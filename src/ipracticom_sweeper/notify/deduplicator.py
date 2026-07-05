"""Alert deduplication: 5-min window, group by fingerprint."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Callable

from .fingerprint import make_fingerprint


@dataclass
class DedupResult:
    should_send: bool
    count: int
    last_seen: float
    first_seen: float


class Deduplicator:
    def __init__(self, window_seconds: float = 300.0):
        self.window = window_seconds
        # In-memory cache: fingerprint -> (first_seen, last_seen, count)
        self._cache: dict[str, tuple[float, float, int]] = {}

    def check(self, host: str, module: str, defcon: int, now: float, force: bool = False) -> DedupResult:
        """Returns whether alert should be sent.

        Logic:
        - DEFCON <= 3 (critical) → always send
        - force=True → always send
        - Same fingerprint within window → suppress, increment count
        - New fingerprint OR window expired → send
        """
        if force or defcon <= 3:
            fp = make_fingerprint(host, module, defcon)
            entry = self._cache.get(fp)
            if entry:
                first, _last, count = entry
                self._cache[fp] = (first, now, count + 1)
                return DedupResult(True, count + 1, now, first)
            self._cache[fp] = (now, now, 1)
            return DedupResult(True, 1, now, now)

        fp = make_fingerprint(host, module, defcon)
        entry = self._cache.get(fp)

        if entry is None:
            self._cache[fp] = (now, now, 1)
            return DedupResult(True, 1, now, now)

        first, last, count = entry
        if (now - last) > self.window:
            # window expired: send as new
            self._cache[fp] = (now, now, 1)
            return DedupResult(True, 1, now, now)

        # within window: suppress, increment
        self._cache[fp] = (first, now, count + 1)
        return DedupResult(False, count + 1, now, first)

    def reset(self) -> None:
        self._cache.clear()

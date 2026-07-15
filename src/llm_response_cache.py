"""Process-local, capacity-exact LRU cache for LLM responses.

The cache deliberately owns no persistence or cross-process coordination. Its
metrics contain counters and configuration values only; cache keys and response
content are never exposed.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import math
import threading
import time
from typing import Callable, Optional


DEFAULT_MAX_ENTRIES = 128
DEFAULT_TTL_SECONDS = 300.0
MAX_TTL_SECONDS = 3600.0


@dataclass(frozen=True)
class _CacheEntry:
    response: str
    expires_at: float


class LLMResponseCache:
    """A thread-safe LRU with bounded TTL and content-free metrics."""

    def __init__(
        self,
        *,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if isinstance(max_entries, bool) or not isinstance(max_entries, int) or max_entries <= 0:
            raise ValueError("max_entries must be a positive integer")
        if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, (int, float)):
            raise ValueError("ttl_seconds must be a positive finite number")
        ttl = float(ttl_seconds)
        if not math.isfinite(ttl) or ttl <= 0 or ttl > MAX_TTL_SECONDS:
            raise ValueError(f"ttl_seconds must be greater than 0 and at most {MAX_TTL_SECONDS:g}")
        if not callable(clock):
            raise ValueError("clock must be callable")

        self._max_entries = max_entries
        self._ttl_seconds = ttl
        self._clock = clock
        self._lock = threading.RLock()
        self._entries: OrderedDict[str, _CacheEntry] = OrderedDict()
        self._hits = 0
        self._misses = 0
        self._expirations = 0
        self._evictions = 0

    @property
    def max_entries(self) -> int:
        return self._max_entries

    @property
    def ttl_seconds(self) -> float:
        return self._ttl_seconds

    def _purge_expired_locked(self, now: float) -> None:
        expired = [key for key, entry in self._entries.items() if now >= entry.expires_at]
        for key in expired:
            self._entries.pop(key)
        self._expirations += len(expired)

    def get(self, cache_key: str) -> Optional[str]:
        """Return and promote a live response, or record a miss."""
        with self._lock:
            self._purge_expired_locked(self._clock())
            entry = self._entries.get(cache_key)
            if entry is None:
                self._misses += 1
                return None
            self._entries.move_to_end(cache_key, last=True)
            self._hits += 1
            return entry.response

    def set(self, cache_key: str, response: str) -> None:
        """Insert or overwrite a response without ever exceeding capacity."""
        with self._lock:
            now = self._clock()
            self._purge_expired_locked(now)

            if cache_key in self._entries:
                self._entries[cache_key] = _CacheEntry(response, now + self._ttl_seconds)
                self._entries.move_to_end(cache_key, last=True)
                return

            # Evict before inserting so even the locked internal state never
            # temporarily exceeds the configured capacity.
            while len(self._entries) >= self._max_entries:
                self._entries.popitem(last=False)
                self._evictions += 1
            self._entries[cache_key] = _CacheEntry(response, now + self._ttl_seconds)

    def clear(self) -> None:
        """Remove all responses while retaining lifetime metric counters."""
        with self._lock:
            self._entries.clear()

    def __len__(self) -> int:
        with self._lock:
            self._purge_expired_locked(self._clock())
            return len(self._entries)

    def metrics(self) -> dict[str, int | float]:
        """Return a content-free, point-in-time metric snapshot."""
        with self._lock:
            self._purge_expired_locked(self._clock())
            return {
                "hits": self._hits,
                "misses": self._misses,
                "expirations": self._expirations,
                "evictions": self._evictions,
                "current_size": len(self._entries),
                "max_entries": self._max_entries,
                "ttl_seconds": self._ttl_seconds,
            }

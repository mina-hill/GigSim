"""Fault injection and per-token rate limiting, so GigBid's error handling has something real
to handle: 429 with Retry-After, random 5xx, latency jitter and a full outage switch."""

from __future__ import annotations

import math
import random
import threading
import time
from dataclasses import dataclass, field


@dataclass
class FaultState:
    error_rate: float = 0.03
    latency_ms_max: int = 150
    down: bool = False
    rate_limit_per_minute: int = 60
    seed: int = 4016
    _rng: random.Random = field(init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        self._rng = random.Random(self.seed)

    def reseed(self, seed: int | None = None) -> None:
        with self._lock:
            self._rng = random.Random(self.seed if seed is None else seed)

    def draw(self) -> tuple[bool, int, float]:
        """(inject an error?, which status, latency seconds) from the seeded generator."""
        with self._lock:
            fail = self._rng.random() < self.error_rate
            status = self._rng.choice([500, 502, 503])
            latency = self._rng.uniform(0, self.latency_ms_max) / 1000.0
        return fail, status, latency

    def chance(self, p: float) -> bool:
        with self._lock:
            return self._rng.random() < p

    def as_dict(self) -> dict:
        return {
            "error_rate": self.error_rate,
            "latency_ms_max": self.latency_ms_max,
            "down": self.down,
            "rate_limit_per_minute": self.rate_limit_per_minute,
        }


class TokenBucketLimiter:
    """One bucket per API token: capacity = limit per minute, refilled continuously."""

    def __init__(self, per_minute: int) -> None:
        self.per_minute = per_minute
        self._buckets: dict[str, tuple[float, float]] = {}  # token -> (tokens, last_ts)
        self._lock = threading.Lock()

    def set_rate(self, per_minute: int) -> None:
        with self._lock:
            self.per_minute = per_minute
            self._buckets.clear()

    def take(self, key: str, now: float | None = None) -> tuple[bool, int]:
        """Try to take one token. Returns (allowed, retry_after_seconds)."""
        now = time.monotonic() if now is None else now
        capacity = float(self.per_minute)
        rate = capacity / 60.0
        with self._lock:
            tokens, last = self._buckets.get(key, (capacity, now))
            tokens = min(capacity, tokens + (now - last) * rate)
            if tokens >= 1.0:
                self._buckets[key] = (tokens - 1.0, now)
                return True, 0
            self._buckets[key] = (tokens, now)
            wait = (1.0 - tokens) / rate if rate > 0 else 60.0
            return False, max(1, math.ceil(wait))

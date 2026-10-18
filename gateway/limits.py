"""In-memory rate limiting and per-key concurrency limits (single process by design)."""

import math
import time
from collections import defaultdict, deque
from dataclasses import dataclass


@dataclass(frozen=True)
class RateResult:
    allowed: bool
    limit: int
    remaining: int
    reset_s: float

    def headers(self) -> dict[str, str]:
        reset = max(1, math.ceil(self.reset_s))
        headers = {
            "x-ratelimit-limit-requests": str(self.limit),
            "x-ratelimit-remaining-requests": str(self.remaining),
            "x-ratelimit-reset-requests": f"{reset}s",
        }
        if not self.allowed:
            headers["Retry-After"] = str(reset)
        return headers


class SlidingWindow:
    """Sliding-window counter keyed by an arbitrary hashable (key id, IP, ...)."""

    def __init__(self, window_s: float = 60.0):
        self.window = window_s
        self._hits: dict[object, deque[float]] = {}

    def _purge(self, key: object, now: float) -> deque[float]:
        dq = self._hits.get(key)
        if dq is None:
            dq = self._hits[key] = deque()
        cutoff = now - self.window
        while dq and dq[0] <= cutoff:
            dq.popleft()
        return dq

    def hit(self, key: object, limit: int, now: float | None = None) -> RateResult:
        now = time.monotonic() if now is None else now
        dq = self._purge(key, now)
        if len(dq) >= limit:
            return RateResult(False, limit, 0, dq[0] + self.window - now)
        dq.append(now)
        return RateResult(True, limit, limit - len(dq), dq[0] + self.window - now)

    def count(self, key: object, now: float | None = None) -> int:
        now = time.monotonic() if now is None else now
        dq = self._purge(key, now)
        if not dq:
            del self._hits[key]
            return 0
        return len(dq)

    def add(self, key: object, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        self._purge(key, now).append(now)
        if len(self._hits) > 50_000:
            for k in [k for k, v in self._hits.items() if not v]:
                del self._hits[k]


class FailedAuthLimiter:
    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self._window = SlidingWindow(60.0)

    def is_blocked(self, ip: str) -> bool:
        return self._window.count(ip) >= self.per_minute

    def record(self, ip: str) -> None:
        self._window.add(ip)


class ConcurrencyGate:
    """Per-key in-flight cap (fail fast). GPU slots are handed out by gateway.scheduler.GpuScheduler."""

    def __init__(self):
        self._per_key: dict[int, int] = defaultdict(int)

    def try_acquire_key(self, key_id: int, limit: int) -> bool:
        if self._per_key[key_id] >= limit:
            return False
        self._per_key[key_id] += 1
        return True

    def release_key(self, key_id: int) -> None:
        self._per_key[key_id] -= 1
        if self._per_key[key_id] <= 0:
            del self._per_key[key_id]

    def snapshot(self) -> dict:
        return {"per_key_in_flight": dict(self._per_key)}

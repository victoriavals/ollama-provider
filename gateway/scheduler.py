"""Model-aware GPU scheduler: the gateway's single queue in front of Ollama (one process by design).

Ollama runs up to OLLAMA_NUM_PARALLEL requests for the loaded model, some architectures (qwen35)
only one, and with OLLAMA_MAX_LOADED_MODELS=1 a request for another model forces an unload and
reload. So requests wait here, in one FIFO lane per model, and Ollama only receives what it can
run right away:

- a lane admits at most its capacity (resolve_capacity), all lanes together at most global_limit;
- one model owns the GPU at a time (its "turn"). The turn stays with the model while it has
  waiters, even between requests, and passes to the model with the oldest waiter only once
  another model is waiting AND the turn is at least switch_after_s old. The running requests
  drain first, then the next model starts. This batches requests per model instead of
  reloading models on every request, and no model starves;
- at most max_queue requests wait; a waiter that is not granted within its timeout expires.

State changes only in synchronous methods on the event loop (enqueue, Ticket.close, expiry
callbacks), each of which dispatches again, so a grant can never race with a cancellation and no
timer is needed for turn changes.
"""

import asyncio
import logging
import time
from collections import deque
from collections.abc import Callable
from typing import Any

from .config import Settings

log = logging.getLogger("gateway.scheduler")

WAITING, GRANTED, RELEASED, EXPIRED, CANCELLED = "waiting", "granted", "released", "expired", "cancelled"


def resolve_capacity(settings: Settings, model: str, architecture: str | None) -> int:
    """Parallel slots for a model: MODEL_PARALLEL, else 1 for SERIAL_ARCHITECTURES, else global."""
    cap = settings.model_parallel_map.get(model)
    if cap is None:
        serial = architecture is not None and architecture.lower() in settings.serial_architecture_set
        cap = 1 if serial else settings.global_concurrency
    return max(1, min(cap, settings.global_concurrency))


class _Lane:
    __slots__ = ("model", "capacity", "running", "waiters")

    def __init__(self, model: str, capacity: int):
        self.model = model
        self.capacity = capacity
        self.running = 0
        self.waiters: deque[Ticket] = deque()


class Ticket:
    """One request's place in the scheduler. Its owner must call close() exactly when it ends."""

    __slots__ = ("_sched", "lane", "state", "enqueued_at", "dequeued_at", "_future", "_timer")

    def __init__(self, sched: "GpuScheduler", lane: _Lane, now: float, future: asyncio.Future):
        self._sched = sched
        self.lane = lane
        self.state = WAITING
        self.enqueued_at = now
        self.dequeued_at: float | None = None
        self._future = future
        self._timer: asyncio.TimerHandle | None = None

    @property
    def model(self) -> str:
        return self.lane.model

    @property
    def queue_ms(self) -> int:
        end = self.dequeued_at if self.dequeued_at is not None else self._sched.clock()
        return int((end - self.enqueued_at) * 1000)

    async def wait(self, timeout: float | None = None) -> bool | None:
        """True once granted, False if expired or closed, None if still queued after `timeout`.

        Cancelling the caller never touches the queue (asyncio.wait does not cancel the future);
        leaving the queue is close()'s job.
        """
        done, _ = await asyncio.wait({self._future}, timeout=timeout)
        return self._future.result() if done else None

    def close(self) -> None:
        """Leave the queue or free the GPU slot. Idempotent."""
        self._sched._close(self)


class GpuScheduler:
    def __init__(self, global_limit: int, max_queue: int, switch_after_s: float,
                 clock: Callable[[], float] = time.monotonic):
        self.global_limit = max(1, global_limit)
        self.max_queue = max(0, max_queue)
        self.switch_after_s = max(0.0, switch_after_s)
        self.clock = clock
        self._lanes: dict[str, _Lane] = {}
        self._turn: _Lane | None = None
        self._turn_started = 0.0
        self.active = 0
        self.waiting = 0
        self.stats = {"granted": 0, "expired": 0, "rejected": 0, "switches": 0}

    def enqueue(self, model: str, capacity: int | None, timeout: float) -> Ticket | None:
        """Queue a request for `model`; None when the queue is full.

        capacity None keeps the lane's current capacity (global_limit for a new lane).
        """
        loop = asyncio.get_running_loop()
        lane = self._lanes.get(model)
        if lane is None:
            lane = self._lanes[model] = _Lane(model, self.global_limit)
        if capacity is not None:
            lane.capacity = max(1, min(capacity, self.global_limit))
        ticket = Ticket(self, lane, self.clock(), loop.create_future())
        lane.waiters.append(ticket)
        self.waiting += 1
        self._dispatch()
        if ticket.state == WAITING:
            if self.waiting > self.max_queue:
                self._leave_queue(ticket, CANCELLED)
                self.stats["rejected"] += 1
                self._dispatch()
                return None
            ticket._timer = loop.call_later(timeout, self._expire, ticket)
        return ticket

    def position(self, ticket: Ticket) -> int | None:
        """1-based position within the ticket's lane, None once it left the queue."""
        if ticket.state != WAITING:
            return None
        return ticket.lane.waiters.index(ticket) + 1

    def snapshot(self) -> dict[str, Any]:
        now = self.clock()
        turn = None
        if self._turn is not None:
            turn = {"model": self._turn.model, "age_s": round(now - self._turn_started, 1),
                    "yielding": self._yielding(self._turn, now)}
        return {
            "global_limit": self.global_limit,
            "active": self.active,
            "waiting": self.waiting,
            "max_queue": self.max_queue,
            "switch_after_s": self.switch_after_s,
            "turn": turn,
            "lanes": [{
                "model": lane.model, "capacity": lane.capacity, "running": lane.running,
                "waiting": len(lane.waiters),
                "oldest_wait_s": round(now - lane.waiters[0].enqueued_at, 1) if lane.waiters else 0.0,
            } for lane in sorted(self._lanes.values(), key=lambda l: l.model)],
            "stats": dict(self.stats),
        }

    # ---- internals (synchronous; every state change dispatches) ----

    def _yielding(self, cur: _Lane, now: float) -> bool:
        others_waiting = any(l.waiters for l in self._lanes.values() if l is not cur)
        return others_waiting and now - self._turn_started >= self.switch_after_s

    def _dispatch(self) -> None:
        now = self.clock()
        while self.active < self.global_limit:
            cur = self._turn
            others = [l for l in self._lanes.values() if l is not cur and l.waiters]
            if cur is not None and cur.waiters and cur.running < cur.capacity and not self._yielding(cur, now):
                self._grant(cur.waiters[0], now)
            elif cur is not None and cur.running > 0:
                return  # lane full or draining for a switch; the next release dispatches again
            elif others:
                nxt = min(others, key=lambda l: l.waiters[0].enqueued_at)
                if cur is not None:
                    self.stats["switches"] += 1
                    log.info("gpu turn %s -> %s (%d waiting)", cur.model, nxt.model, self.waiting)
                self._turn, self._turn_started = nxt, now
                self._grant(nxt.waiters[0], now)  # grant before re-checking, so switch_after_s=0 cannot spin
            else:
                return

    def _grant(self, ticket: Ticket, now: float) -> None:
        lane = ticket.lane
        lane.waiters.remove(ticket)
        self.waiting -= 1
        lane.running += 1
        self.active += 1
        ticket.state = GRANTED
        ticket.dequeued_at = now
        if ticket._timer is not None:
            ticket._timer.cancel()
        ticket._future.set_result(True)
        self.stats["granted"] += 1

    def _leave_queue(self, ticket: Ticket, state: str) -> None:
        ticket.lane.waiters.remove(ticket)
        self.waiting -= 1
        ticket.state = state
        ticket.dequeued_at = self.clock()
        if ticket._timer is not None:
            ticket._timer.cancel()
        ticket._future.set_result(False)

    def _expire(self, ticket: Ticket) -> None:
        if ticket.state != WAITING:
            return
        self._leave_queue(ticket, EXPIRED)
        self.stats["expired"] += 1
        self._dispatch()

    def _close(self, ticket: Ticket) -> None:
        if ticket.state == WAITING:
            self._leave_queue(ticket, CANCELLED)
        elif ticket.state == GRANTED:
            ticket.state = RELEASED
            ticket.lane.running -= 1
            self.active -= 1
        else:
            return
        self._dispatch()

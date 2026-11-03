"""Model-aware GPU scheduler: the gateway's single queue in front of Ollama (one process by design).

Ollama runs up to OLLAMA_NUM_PARALLEL requests for the loaded model, some architectures (qwen35)
only one, and with OLLAMA_MAX_LOADED_MODELS=1 a request for another model forces an unload and
reload. So requests wait here, in one FIFO lane per model, and Ollama only receives what it can
run right away:

- a lane admits at most its capacity (resolve_capacity), all lanes together at most global_limit;
- within a lane the next request is the one with the highest priority, then the one whose owner
  (API key) has the fewest requests running in that lane, then the oldest. A key that floods
  the queue therefore cannot make other keys wait behind all of its requests;
- priority requests (priority > 0) get extra room: reserved_slots of every lane with more slots
  than that are kept for them, they are never rejected for a full queue, and a priority request
  waiting for another model makes the running model yield at once (its running requests still
  finish; nothing is preempted);
- one model owns the GPU at a time (its "turn"). The turn stays with the model while it has
  waiters, even between requests, and passes to the model with the oldest waiter only once
  another model is waiting AND the turn is at least switch_after_s old (counted from the turn's
  first request after being idle). The running requests drain first, then the next model starts.
  This batches requests per model instead of reloading models on every request, and no model
  starves;
- at most max_queue requests wait; a waiter that is not granted within its timeout expires;
- close() (shutdown) releases all waiters at once and refuses new requests.

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
    __slots__ = ("model", "capacity", "running", "running_by_owner", "waiters")

    def __init__(self, model: str, capacity: int):
        self.model = model
        self.capacity = capacity
        self.running = 0
        self.running_by_owner: dict[object, int] = {}
        self.waiters: deque[Ticket] = deque()  # arrival order; selection order is _order()

    def _order(self, ticket: "Ticket") -> tuple:
        return (-ticket.priority, self.running_by_owner.get(ticket.owner, 0), ticket.enqueued_at)

    def next_waiter(self) -> "Ticket":
        return min(self.waiters, key=self._order)  # min() keeps arrival order on ties

    def best_priority(self) -> int:
        return max(t.priority for t in self.waiters)


class Ticket:
    """One request's place in the scheduler. Its owner must call close() exactly when it ends."""

    __slots__ = ("_sched", "lane", "owner", "priority", "state", "enqueued_at", "dequeued_at", "_future", "_timer")

    def __init__(self, sched: "GpuScheduler", lane: _Lane, now: float, future: asyncio.Future,
                 owner: object = None, priority: int = 0):
        self._sched = sched
        self.lane = lane
        self.owner = owner  # API key id (None: internal, e.g. warm-up)
        self.priority = priority
        self.state = WAITING
        self.enqueued_at = now
        self.dequeued_at: float | None = None
        self._future = future
        self._timer: asyncio.TimerHandle | None = None

    @property
    def model(self) -> str:
        return self.lane.model

    @property
    def granted(self) -> bool:
        return self.state == GRANTED

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
                 clock: Callable[[], float] = time.monotonic, reserved_slots: int = 0):
        self.global_limit = max(1, global_limit)
        self.max_queue = max(0, max_queue)
        self.switch_after_s = max(0.0, switch_after_s)
        self.reserved_slots = max(0, reserved_slots)
        self.clock = clock
        self._lanes: dict[str, _Lane] = {}
        self._turn: _Lane | None = None
        self._turn_started = 0.0
        self.active = 0
        self.waiting = 0
        self.closed = False
        self.stats = {"granted": 0, "expired": 0, "rejected": 0, "switches": 0}

    def enqueue(self, model: str, capacity: int | None, timeout: float,
                owner: object = None, priority: int = 0) -> Ticket | None:
        """Queue a request for `model`; None when the queue is full or the scheduler is closed.

        capacity None keeps the lane's current capacity (global_limit for a new lane).
        owner groups requests for fairness (the API key id); a higher priority goes first.
        """
        if self.closed:
            self.stats["rejected"] += 1
            return None
        loop = asyncio.get_running_loop()
        lane = self._lanes.get(model)
        if lane is None:
            lane = self._lanes[model] = _Lane(model, self.global_limit)
        if capacity is not None:
            lane.capacity = max(1, min(capacity, self.global_limit))
        ticket = Ticket(self, lane, self.clock(), loop.create_future(), owner, priority)
        lane.waiters.append(ticket)
        self.waiting += 1
        self._dispatch()
        if ticket.state == WAITING:
            if priority <= 0 and self._normal_waiting() > self.max_queue:
                self._leave_queue(ticket, CANCELLED)
                self.stats["rejected"] += 1
                self._dispatch()
                return None
            ticket._timer = loop.call_later(timeout, self._expire, ticket)
        return ticket

    def close(self) -> None:
        """Shutdown: refuse new requests and release every waiter at once (running ones finish).

        Queued clients then get a clean 503 instead of being cancelled by the server's
        graceful-shutdown timeout. Call it on the event loop thread.
        """
        self.closed = True
        for lane in self._lanes.values():
            for ticket in list(lane.waiters):
                self._leave_queue(ticket, CANCELLED)

    def position(self, ticket: Ticket) -> int | None:
        """1-based position within the ticket's lane, None once it left the queue."""
        if ticket.state != WAITING:
            return None
        lane = ticket.lane
        return sorted(lane.waiters, key=lane._order).index(ticket) + 1

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
            "closed": self.closed,
            "reserved_slots": self.reserved_slots,
            "max_queue": self.max_queue,
            "switch_after_s": self.switch_after_s,
            "turn": turn,
            "lanes": [{
                "model": lane.model, "capacity": lane.capacity, "reserved": self._reserved(lane), "running": lane.running,
                "waiting": len(lane.waiters),
                "oldest_wait_s": round(now - lane.waiters[0].enqueued_at, 1) if lane.waiters else 0.0,
                "priority_waiting": sum(1 for t in lane.waiters if t.priority > 0),
            } for lane in sorted(self._lanes.values(), key=lambda l: l.model)],
            "stats": dict(self.stats),
        }

    # ---- internals (synchronous; every state change dispatches) ----

    def _yielding(self, cur: _Lane, now: float) -> bool:
        others = [l for l in self._lanes.values() if l is not cur and l.waiters]
        if not others:
            return False
        if now - self._turn_started >= self.switch_after_s:
            return True
        if now == self._turn_started:
            return False  # a turn that starts in this dispatch fills its free slots first
        # A higher-priority request for another model does not wait for the turn to age.
        mine = cur.best_priority() if cur.waiters else 0
        return max(l.best_priority() for l in others) > mine

    def _reserved(self, lane: _Lane) -> int:
        # Lanes with a single slot (qwen35) cannot spare one: normal keys would never run there.
        return self.reserved_slots if lane.capacity > self.reserved_slots else 0

    def _grantable(self, lane: _Lane, ticket: Ticket) -> bool:
        limit = lane.capacity if ticket.priority > 0 else lane.capacity - self._reserved(lane)
        return lane.running < limit

    def _normal_waiting(self) -> int:
        return sum(1 for lane in self._lanes.values() for t in lane.waiters if t.priority <= 0)

    def _dispatch(self) -> None:
        now = self.clock()
        while self.active < self.global_limit:
            cur = self._turn
            others = [l for l in self._lanes.values() if l is not cur and l.waiters]
            if cur is not None and cur.waiters and self._grantable(cur, cur.next_waiter()) and not self._yielding(cur, now):
                if cur.running == 0 and not others:
                    self._turn_started = now  # idle time does not count towards the turn
                self._grant(cur.next_waiter(), now)
            elif cur is not None and cur.running > 0:
                return  # lane full or draining for a switch; the next release dispatches again
            elif others:
                # Priority waiters first, then the model that has waited longest.
                nxt = min(others, key=lambda l: (-l.best_priority(), l.waiters[0].enqueued_at))
                if cur is not None:
                    self.stats["switches"] += 1
                    log.info("gpu turn %s -> %s (%d waiting)", cur.model, nxt.model, self.waiting)
                self._turn, self._turn_started = nxt, now
                self._grant(nxt.next_waiter(), now)  # grant before re-checking, so switch_after_s=0 cannot spin
            else:
                return

    def _grant(self, ticket: Ticket, now: float) -> None:
        lane = ticket.lane
        lane.waiters.remove(ticket)
        self.waiting -= 1
        lane.running += 1
        lane.running_by_owner[ticket.owner] = lane.running_by_owner.get(ticket.owner, 0) + 1
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
            lane = ticket.lane
            lane.running -= 1
            left = lane.running_by_owner[ticket.owner] - 1
            if left:
                lane.running_by_owner[ticket.owner] = left
            else:
                del lane.running_by_owner[ticket.owner]
            self.active -= 1
        else:
            return
        self._dispatch()

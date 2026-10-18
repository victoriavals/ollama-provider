import asyncio
import json

import httpx
import pytest

from gateway.apps.public import create_public_app
from gateway.config import Settings
from gateway.proxy import RequestContext, stream_sse
from gateway.scheduler import GpuScheduler, resolve_capacity
from tests.conftest import fixture_json, flush_usage, hold_slots, new_key, scheduler_idle

QWEN = {"model": "qwen3.5:4b", "messages": [{"role": "user", "content": "hi"}]}
GEMMA = {"model": "gemma3:4b", "messages": [{"role": "user", "content": "hi"}]}


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def make(global_limit=4, max_queue=32, switch_after_s=10.0) -> tuple[GpuScheduler, Clock]:
    clock = Clock()
    return GpuScheduler(global_limit, max_queue, switch_after_s, clock=clock), clock


def states(tickets) -> list[str]:
    return [t.state for t in tickets]


async def wait_until(cond, timeout: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not cond():
        assert asyncio.get_running_loop().time() < deadline, "condition not reached"
        await asyncio.sleep(0.01)


# ---- unit: scheduling rules ----

async def test_fifo_within_a_lane_up_to_the_global_limit():
    s, _ = make(global_limit=2)
    t = [s.enqueue("a", None, 60) for _ in range(4)]
    assert states(t) == ["granted", "granted", "waiting", "waiting"]
    assert (s.position(t[2]), s.position(t[3]), s.position(t[0])) == (1, 2, None)
    t[1].close()
    assert states(t)[1:] == ["released", "granted", "waiting"]
    assert await t[2].wait() is True
    for ticket in t:
        ticket.close()
    assert (s.active, s.waiting) == (0, 0)


async def test_serial_lane_runs_one_request_even_with_free_slots():
    s, _ = make()
    q = [s.enqueue("qwen", 1, 60) for _ in range(3)]
    assert states(q) == ["granted", "waiting", "waiting"] and s.active == 1
    q[0].close()
    assert states(q) == ["released", "granted", "waiting"]


async def test_other_model_waits_while_the_turn_model_runs():
    s, _ = make()
    a = s.enqueue("a", None, 60)
    b = s.enqueue("b", None, 60)
    assert (a.state, b.state) == ("granted", "waiting")
    a.close()  # a's lane is idle and empty: hand over at once
    assert b.state == "granted" and s.stats["switches"] == 1


async def test_turn_model_admits_until_its_turn_expires_then_drains():
    s, clock = make(switch_after_s=10)
    a1 = s.enqueue("a", None, 60)
    b = s.enqueue("b", None, 60)
    clock.t += 5
    a2 = s.enqueue("a", None, 60)
    assert a2.state == "granted"  # turn is 5 s old: a keeps the GPU
    clock.t += 6
    a3 = s.enqueue("a", None, 60)
    assert a3.state == "waiting"  # turn is 11 s old and b waits: a drains
    a1.close()
    assert (b.state, a3.state) == ("waiting", "waiting")  # a2 still running
    a2.close()
    assert (b.state, a3.state) == ("granted", "waiting")
    assert s.snapshot()["turn"]["model"] == "b"
    b.close()
    assert a3.state == "granted"
    assert s.stats["switches"] == 2


async def test_serial_lane_keeps_its_turn_between_requests():
    s, clock = make()
    q1, q2 = s.enqueue("qwen", 1, 60), s.enqueue("qwen", 1, 60)
    g = s.enqueue("gemma", None, 60)
    clock.t += 3
    q1.close()
    assert (q2.state, g.state) == ("granted", "waiting")  # no reload for one request
    clock.t += 8
    q3 = s.enqueue("qwen", 1, 60)
    q2.close()
    assert (g.state, q3.state) == ("granted", "waiting")  # turn expired: gemma's turn


async def test_idle_time_does_not_count_towards_the_turn():
    s, clock = make(switch_after_s=10)
    s.enqueue("gemma", None, 60).close()  # gemma owns the turn, then the GPU sits idle
    clock.t += 3600
    burst = [s.enqueue("gemma", None, 60) for _ in range(4)]
    q = s.enqueue("qwen", 1, 60)
    g5 = s.enqueue("gemma", None, 60)
    clock.t += 1
    burst[0].close()
    assert g5.state == "granted" and q.state == "waiting"  # turn is 1 s old, not 3601 s
    assert s.snapshot()["turn"]["age_s"] == 1.0


async def test_close_releases_waiters_and_refuses_new_requests():
    s, _ = make(global_limit=1)
    running = s.enqueue("a", None, 60)
    waiting = [s.enqueue("a", None, 60), s.enqueue("b", None, 60)]
    s.close()
    assert running.state == "granted" and states(waiting) == ["cancelled", "cancelled"]
    assert [await t.wait() for t in waiting] == [False, False]
    assert s.enqueue("a", None, 60) is None and s.snapshot()["closed"] is True
    running.close()
    assert (s.active, s.waiting) == (0, 0)


async def test_expired_waiter_of_another_model_unblocks_the_turn_model():
    s, _ = make(switch_after_s=0)
    s.enqueue("a", None, 60)
    b = s.enqueue("b", None, 0.05)
    a2 = s.enqueue("a", None, 60)
    assert a2.state == "waiting"  # a drains for b
    assert await b.wait() is False and b.state == "expired"
    assert a2.state == "granted" and s.stats["expired"] == 1


async def test_close_is_idempotent_in_every_state():
    s, _ = make(global_limit=1)
    granted = s.enqueue("a", None, 60)
    waiting = s.enqueue("a", None, 60)
    expiring = s.enqueue("a", None, 0.01)
    assert await expiring.wait() is False
    waiting.close()
    waiting.close()
    assert waiting.state == "cancelled" and await waiting.wait() is False
    granted.close()
    granted.close()
    expiring.close()
    assert (s.active, s.waiting) == (0, 0)
    assert [lane["running"] for lane in s.snapshot()["lanes"]] == [0]


async def test_full_queue_rejects_but_a_free_slot_is_still_granted():
    s, _ = make(global_limit=1, max_queue=1)
    s.enqueue("a", None, 60)
    s.enqueue("a", None, 60)
    assert s.enqueue("a", None, 60) is None
    assert (s.stats["rejected"], s.waiting) == (1, 1)
    s0, _ = make(max_queue=0)
    assert s0.enqueue("a", None, 60).state == "granted"


async def test_lower_capacity_blocks_admission_until_drained():
    s, _ = make()
    running = [s.enqueue("m", None, 60) for _ in range(3)]
    waiter = s.enqueue("m", 1, 60)
    assert states(running) == ["granted"] * 3 and waiter.state == "waiting"
    running[0].close()
    running[1].close()
    assert waiter.state == "waiting"
    running[2].close()
    assert waiter.state == "granted"


async def test_switch_after_zero_alternates_without_spinning():
    s, _ = make(global_limit=2, switch_after_s=0)
    a = [s.enqueue("a", None, 60) for _ in range(3)]
    b = [s.enqueue("b", None, 60) for _ in range(2)]
    assert states(a) == ["granted", "granted", "waiting"] and states(b) == ["waiting", "waiting"]
    a[0].close()
    a[1].close()
    assert states(b) == ["granted", "waiting"] and a[2].state == "waiting"
    b[0].close()
    assert a[2].state == "granted" and b[1].state == "waiting"
    a[2].close()
    assert b[1].state == "granted" and s.stats["switches"] == 3


async def test_queue_ms_and_snapshot():
    s, clock = make()
    q1, q2 = s.enqueue("qwen", 1, 60), s.enqueue("qwen", 1, 60)
    clock.t += 2.5
    snap = s.snapshot()
    assert (snap["global_limit"], snap["active"], snap["waiting"]) == (4, 1, 1)
    assert snap["turn"] == {"model": "qwen", "age_s": 2.5, "yielding": False}
    assert snap["lanes"] == [{"model": "qwen", "capacity": 1, "running": 1, "waiting": 1, "oldest_wait_s": 2.5}]
    q1.close()
    clock.t += 3
    assert (q1.queue_ms, q2.queue_ms) == (0, 2500)


def test_resolve_capacity():
    s = Settings(_env_file=None, global_concurrency=4, model_parallel="special:1b=2, big:7b=9",
                 serial_architectures="qwen35,Mamba")
    assert resolve_capacity(s, "gemma3:4b", "gemma3") == 4
    assert resolve_capacity(s, "qwen3.5:4b", "qwen35") == 1
    assert resolve_capacity(s, "x", "mamba") == 1
    assert resolve_capacity(s, "special:1b", "qwen35") == 2  # explicit setting wins
    assert resolve_capacity(s, "big:7b", None) == 4  # clamped to the GPU
    assert resolve_capacity(s, "unknown", None) == 4


@pytest.mark.parametrize("bad", ["qwen3.5:4b", "qwen3.5:4b=0", "=2", "a=x"])
def test_model_parallel_is_validated(bad):
    with pytest.raises(ValueError):
        Settings(_env_file=None, model_parallel=bad)


@pytest.mark.parametrize("field", ["queue_timeout_s", "model_switch_after_s"])
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1.0])
def test_queue_settings_must_be_finite_and_non_negative(field, bad):
    with pytest.raises(ValueError):
        Settings(_env_file=None, **{field: bad})


# ---- integration: through the public app ----

def hold_upstream(ollama):
    """Mock chat completions that stay in flight until the returned event is set."""
    release = asyncio.Event()
    calls: list[str] = []

    async def slow(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content)["model"])
        await release.wait()
        return httpx.Response(200, json=fixture_json("chat.json"))
    ollama.post("/v1/chat/completions").mock(side_effect=slow)
    return release, calls


async def test_model_info_reports_architecture(gw):
    info = await gw.ollama.model_info("qwen3.5:4b")
    assert info.architecture == "qwen35" and "thinking" in info.capabilities
    assert await gw.ollama.capabilities("gemma3:4b") == {"completion", "vision"}


async def test_serial_model_reaches_ollama_one_request_at_a_time(public, gw, ollama):
    gw.settings.queue_timeout_s = 5
    await gw.db.set_model_enabled("qwen3.5:4b", True)
    _, key = await new_key(gw, max_concurrent=4)
    auth = {"Authorization": f"Bearer {key}"}
    release, calls = hold_upstream(ollama)
    reqs = [asyncio.create_task(public.post("/v1/chat/completions", headers=auth, json=QWEN)) for _ in range(2)]
    await wait_until(lambda: len(calls) == 1 and gw.scheduler.waiting == 1)
    await asyncio.sleep(0.05)
    assert len(calls) == 1  # 2 global slots free, but qwen35 runs one at a time
    lane = gw.scheduler.snapshot()["lanes"][-1]
    assert (lane["model"], lane["capacity"], lane["running"], lane["waiting"]) == ("qwen3.5:4b", 1, 1, 1)
    assert lane["oldest_wait_s"] > 0
    release.set()
    assert [r.status_code for r in await asyncio.gather(*reqs)] == [200, 200]
    assert len(calls) == 2 and scheduler_idle(gw)


async def test_other_model_is_not_sent_while_the_loaded_model_runs(public, gw, ollama):
    gw.settings.queue_timeout_s = 5
    await gw.db.set_model_enabled("qwen3.5:4b", True)
    _, key = await new_key(gw, max_concurrent=4)
    auth = {"Authorization": f"Bearer {key}"}
    release, calls = hold_upstream(ollama)
    gemma = asyncio.create_task(public.post("/v1/chat/completions", headers=auth, json=GEMMA))
    await wait_until(lambda: calls == ["gemma3:4b"])
    qwen = asyncio.create_task(public.post("/v1/chat/completions", headers=auth, json=QWEN))
    await wait_until(lambda: gw.scheduler.waiting == 1)
    await asyncio.sleep(0.05)
    assert calls == ["gemma3:4b"]  # qwen waits in the gateway, not inside Ollama
    release.set()
    assert [r.status_code for r in await asyncio.gather(gemma, qwen)] == [200, 200]
    assert calls == ["gemma3:4b", "qwen3.5:4b"] and scheduler_idle(gw)


async def test_full_queue_returns_a_real_503_for_streams(public, auth, gw, ollama):
    route = ollama.post("/v1/chat/completions").respond(json=fixture_json("chat.json"))
    gw.scheduler.max_queue = 0
    held = hold_slots(gw)
    r = await public.post("/v1/chat/completions", headers=auth, json={**GEMMA, "stream": True})
    assert r.status_code == 503 and r.json()["error"]["code"] == "server_busy"
    assert "queue is full" in r.json()["error"]["message"] and r.headers["Retry-After"] == "5"
    assert r.headers["x-ratelimit-limit-requests"] == "100"
    for ticket in held:
        ticket.close()
    assert not route.called and scheduler_idle(gw)
    [row] = await flush_usage(gw)
    assert row["status_code"] == 503 and row["stream"] == 1


async def test_client_leaving_while_queued_frees_its_place(gw, ollama, api_key):
    route = ollama.post("/v1/chat/completions").respond(json=fixture_json("chat.json"))
    held = hold_slots(gw)
    key = await gw.lookup_key(api_key)
    assert gw.gate.try_acquire_key(key.id, 2)
    ctx = RequestContext(key=key, endpoint="chat", model="gemma3:4b", request_id="req_q", client_ip="1.1.1.1")
    resp = stream_sse(gw, "/v1/chat/completions", {**GEMMA, "stream": True}, ctx, include_usage=False)
    it = resp.body_iterator
    assert await it.__anext__() == ": connected\n\n"
    assert await it.__anext__() == ": ping queue=1\n\n"
    assert gw.scheduler.waiting == 1
    await it.aclose()  # what ClosingStreamingResponse does when the client goes away
    assert gw.scheduler.waiting == 0
    for ticket in held:
        ticket.close()
    assert scheduler_idle(gw) and not route.called
    assert ctx.access["status"] == 499 and ctx.access["queue_ms"] >= 0
    [row] = await flush_usage(gw)
    assert row["status_code"] == 499


async def test_warmup_is_skipped_at_once_while_another_model_runs(gw, ollama):
    route = ollama.post("/api/generate").respond(json={"response": "hi", "done": True})
    held = hold_slots(gw, "qwen3.5:4b", 1)
    await asyncio.wait_for(gw.warmup(), 0.1)  # must not sit in the queue (QUEUE_TIMEOUT_S is 0.3 s here)
    snap = gw.scheduler.snapshot()
    assert not route.called and snap["waiting"] == 0  # no waiter left behind
    assert snap["turn"] == {"model": "qwen3.5:4b", "age_s": snap["turn"]["age_s"], "yielding": False}
    held[0].close()


async def test_shutdown_releases_queued_requests_with_a_clean_503(public, auth, gw, ollama):
    route = ollama.post("/v1/chat/completions").respond(json=fixture_json("chat.json"))
    gw.settings.queue_timeout_s = 5
    held = hold_slots(gw)
    ns = asyncio.create_task(public.post("/v1/chat/completions", headers=auth, json=GEMMA))
    st = asyncio.create_task(public.post("/v1/chat/completions", headers=auth, json={**GEMMA, "stream": True}))
    await wait_until(lambda: gw.scheduler.waiting == 2)
    gw.scheduler.close()  # what the SIGTERM handler schedules
    r_ns, r_st = await asyncio.gather(ns, st)
    assert r_ns.status_code == 503 and "restarting" in r_ns.json()["error"]["message"]
    assert r_ns.headers["Retry-After"] == "15"
    assert "restarting" in json.loads(r_st.text.split("data: ")[-1])["error"]["message"]
    late = await public.post("/v1/chat/completions", headers=auth, json={**GEMMA, "stream": True})
    assert late.status_code == 503 and "restarting" in late.json()["error"]["message"]
    for ticket in held:
        ticket.close()
    assert not route.called and scheduler_idle(gw)


async def test_disconnect_while_queued_through_listen_for_disconnect(gw, ollama, api_key):
    # Production path: uvicorn 0.54 reports ASGI spec 2.3, so Starlette cancels the stream task on
    # http.disconnect (CancelledError) instead of closing the generator (GeneratorExit).
    route = ollama.post("/v1/chat/completions").respond(json=fixture_json("chat.json"))
    held = hold_slots(gw)
    app = create_public_app(gw)
    raw = json.dumps({**GEMMA, "stream": True}).encode()
    gone = asyncio.Event()
    received = False

    async def receive():
        nonlocal received
        if not received:
            received = True
            return {"type": "http.request", "body": raw, "more_body": False}
        await gone.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        pass

    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"}, "http_version": "1.1",
             "method": "POST", "scheme": "http", "path": "/v1/chat/completions", "raw_path": b"/v1/chat/completions",
             "query_string": b"", "root_path": "", "client": ("127.0.0.1", 5555), "server": ("127.0.0.1", 8000),
             "headers": [(b"host", b"public"), (b"content-type", b"application/json"),
                         (b"authorization", f"Bearer {api_key}".encode()), (b"content-length", str(len(raw)).encode())]}
    task = asyncio.create_task(app(scope, receive, send))
    await wait_until(lambda: gw.scheduler.waiting == 1)
    gone.set()
    await asyncio.wait_for(task, 2)
    assert gw.scheduler.waiting == 0
    for ticket in held:
        ticket.close()
    assert scheduler_idle(gw) and not route.called
    [row] = await flush_usage(gw)
    assert row["status_code"] == 499


async def test_key_slot_is_not_leaked_when_capacity_resolution_fails(gw, auth, monkeypatch):
    def boom(*args):
        raise RuntimeError("bad architecture")
    monkeypatch.setattr("gateway.apps.public.resolve_capacity", boom)
    transport = httpx.ASGITransport(app=create_public_app(gw), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://public") as client:
        r = await client.post("/v1/chat/completions", headers=auth, json=GEMMA)
    assert r.status_code == 500 and scheduler_idle(gw)


async def test_warmup_goes_through_the_scheduler(gw, ollama):
    seen: list[int] = []

    def generate(request: httpx.Request) -> httpx.Response:
        seen.append(gw.scheduler.active)
        return httpx.Response(200, json={"response": "hi", "done": True})
    route = ollama.post("/api/generate").mock(side_effect=generate)
    await gw.warmup()
    assert seen == [1] and scheduler_idle(gw)

    gw.scheduler.max_queue = 0
    held = hold_slots(gw)
    await gw.warmup()  # GPU busy and no queue room: skipped, never swaps models under load
    assert route.call_count == 1
    for ticket in held:
        ticket.close()

"""Per-key policy: token quotas, expiry, model allowlist, queue priority, and the DB migration."""

import asyncio
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import httpx

from gateway.db import Database, UsageRecord, utcnow
from gateway.quota import QuotaTracker, local_periods
from tests.conftest import fixture_json, flush_usage, hold_slots, new_key, scheduler_idle

CHAT = {"model": "gemma3:4b", "messages": [{"role": "user", "content": "hi"}]}  # fixture usage: 23 tokens
PAST = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(timespec="seconds")


def bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


# ---- migration ---------------------------------------------------------------

async def test_existing_database_gets_new_key_columns(tmp_path):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as conn:  # schema as shipped before key policies existed
        conn.execute("CREATE TABLE api_keys (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,"
                     " key_hash TEXT NOT NULL UNIQUE, prefix TEXT NOT NULL, rpm_limit INTEGER, max_concurrent INTEGER,"
                     " created_at TEXT NOT NULL, last_used_at TEXT, revoked_at TEXT)")
        conn.execute("INSERT INTO api_keys (name, key_hash, prefix, rpm_limit, created_at)"
                     " VALUES ('owner', 'h', 'sk-abc', 30, '2026-10-01T00:00:00+00:00')")
    db = Database(path)
    try:  # always close: an open aiosqlite thread would keep pytest from exiting
        await db.connect()
        [row] = await db.list_keys()
        assert (row["name"], row["rpm_limit"], row["priority"]) == ("owner", 30, 0)
        assert row["daily_token_limit"] is row["monthly_token_limit"] is row["expires_at"] is row["allowed_models"] is None
        await db.close()
        await db.connect()  # second start: nothing left to migrate
    finally:
        await db.close()


# ---- admin API -----------------------------------------------------------------

async def test_admin_creates_and_updates_key_policy(admin):
    r = await admin.post("/admin/keys", json={
        "name": "client-a", "daily_token_limit": 100_000, "monthly_token_limit": 2_000_000,
        "expires_at": "2099-12-31", "allowed_models": ["gemma3:4b", "gemma3:4b"], "priority": 1})
    assert r.status_code == 201
    k = r.json()
    assert (k["daily_token_limit"], k["monthly_token_limit"], k["priority"]) == (100_000, 2_000_000, 1)
    assert k["allowed_models"] == ["gemma3:4b"] and k["expired"] is False
    assert k["expires_at"].endswith("+00:00") and k["expires_at"] > "2099-12-31"
    assert k["usage"] == {"day_tokens": 0, "month_tokens": 0}

    r = await admin.patch(f"/admin/keys/{k['id']}", json={"allowed_models": None, "priority": None, "expires_at": PAST})
    k = r.json()
    assert (k["allowed_models"], k["priority"], k["expired"]) == (None, 0, True)
    assert k["daily_token_limit"] == 100_000  # untouched fields stay

    listing = (await admin.get("/admin/keys")).json()
    assert set(listing["quota_resets"]) == {"day", "month"}


async def test_admin_validates_key_policy(admin):
    for body, param in [({"allowed_models": ["nope:1b"]}, "allowed_models"), ({"allowed_models": []}, "allowed_models"),
                        ({"expires_at": "31-12-2099"}, "expires_at"), ({"priority": 10}, "priority"),
                        ({"daily_token_limit": 0}, "daily_token_limit")]:
        r = await admin.post("/admin/keys", json={"name": "x", **body})
        assert r.status_code == 400 and param in (r.json()["error"]["param"] or ""), body


async def test_admin_lists_token_usage_per_key(admin, gw, public, ollama):
    ollama.post("/v1/chat/completions").respond(json=fixture_json("chat.json"))
    key_id, key = await new_key(gw, name="metered")
    assert (await public.post("/v1/chat/completions", headers=bearer(key), json=CHAT)).status_code == 200
    await flush_usage(gw)
    rows = {k["id"]: k for k in (await admin.get("/admin/keys")).json()["data"]}
    assert rows[key_id]["usage"] == {"day_tokens": 23, "month_tokens": 23}


# ---- expiry ----------------------------------------------------------------------

async def test_expired_key_is_rejected_without_failed_auth_strikes(public, gw):
    _, key = await new_key(gw, expires_at=PAST)
    for _ in range(gw.settings.failed_auth_per_ip_per_min + 2):
        r = await public.get("/v1/models", headers=bearer(key))
        assert r.status_code == 401 and r.json()["error"]["code"] == "api_key_expired"
    _, good = await new_key(gw)
    assert (await public.get("/v1/models", headers=bearer(good))).status_code == 200  # IP not blocked


# ---- model allowlist ---------------------------------------------------------------

async def test_model_allowlist_filters_models_and_requests(public, gw, ollama):
    route = ollama.post("/v1/embeddings").respond(json={"data": []})
    _, key = await new_key(gw, allowed_models=["gemma3:4b"])
    models = (await public.get("/v1/models", headers=bearer(key))).json()["data"]
    assert [m["id"] for m in models] == ["gemma3:4b"]
    r = await public.post("/v1/embeddings", headers=bearer(key), json={"model": "nomic-embed-text", "input": "x"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "model_not_found"
    assert not route.called


# ---- token quota ---------------------------------------------------------------------

async def test_daily_quota_blocks_after_it_is_used_up(public, gw, ollama):
    ollama.post("/v1/chat/completions").respond(json=fixture_json("chat.json"))
    _, key = await new_key(gw, daily_token_limit=30)
    r1 = await public.post("/v1/chat/completions", headers=bearer(key), json=CHAT)
    assert r1.status_code == 200
    assert (r1.headers["x-quota-tokens-day-limit"], r1.headers["x-quota-tokens-day-remaining"]) == ("30", "30")
    r2 = await public.post("/v1/chat/completions", headers=bearer(key), json=CHAT)
    assert r2.status_code == 200 and r2.headers["x-quota-tokens-day-remaining"] == "7"  # soft: may overshoot
    r3 = await public.post("/v1/chat/completions", headers=bearer(key), json=CHAT)
    err = r3.json()["error"]
    assert r3.status_code == 429 and (err["code"], err["type"]) == ("insufficient_quota", "insufficient_quota")
    assert "daily" in err["message"] and r3.headers["x-should-retry"] == "false"
    assert int(r3.headers["Retry-After"]) > 0 and r3.headers["x-quota-tokens-day-remaining"] == "0"
    assert "x-quota-tokens-month-limit" not in r3.headers
    rows = await flush_usage(gw)
    assert [row["status_code"] for row in rows] == [200, 200, 429]


async def test_monthly_quota_counts_usage_already_in_the_database(public, gw, ollama):
    route = ollama.post("/v1/chat/completions").respond(json=fixture_json("chat.json"))
    key_id, key = await new_key(gw, monthly_token_limit=40)
    await gw.db.insert_usage([UsageRecord(key_id=key_id, endpoint="chat", model="gemma3:4b", status_code=200,
                                          total_tokens=50, ts=utcnow())])
    r = await public.post("/v1/chat/completions", headers=bearer(key), json={**CHAT, "stream": True})
    assert r.status_code == 429 and "monthly" in r.json()["error"]["message"]
    assert not route.called


async def test_stream_usage_counts_towards_the_quota(public, gw, ollama):
    from tests.conftest import fixture_text
    ollama.post("/v1/chat/completions").respond(200, content=fixture_text("chat_stream.txt").encode())
    _, key = await new_key(gw, daily_token_limit=20)
    assert (await public.post("/v1/chat/completions", headers=bearer(key), json={**CHAT, "stream": True})).status_code == 200
    r = await public.post("/v1/chat/completions", headers=bearer(key), json=CHAT)
    assert r.status_code == 429  # the stream's 22 tokens were counted


class _FakeUsageDB:
    def __init__(self):
        self.calls = []

    async def token_usage_since(self, key_id, since):
        self.calls.append(since)
        return 100


async def test_quota_periods_roll_over_in_local_time():
    wib = timezone(timedelta(hours=7))
    now = [datetime(2026, 12, 31, 23, 50, tzinfo=wib)]
    db = _FakeUsageDB()
    tracker = QuotaTracker(db, now=lambda: now[0])

    class Key:
        id, daily_token_limit, monthly_token_limit = 1, 1000, 5000
    st = await tracker.status(Key)
    assert (st.day_used, st.month_used) == (100, 100)
    assert st.day_reset == datetime(2027, 1, 1, tzinfo=wib) == st.month_reset
    assert db.calls == ["2026-12-30T17:00:00+00:00", "2026-11-30T17:00:00+00:00"]
    tracker.add(1, 25, "2026-12-31T16:55:00+00:00")
    assert ((await tracker.status(Key)).day_used, len(db.calls)) == (125, 2)  # cached, no new query
    now[0] = datetime(2027, 1, 1, 0, 5, tzinfo=wib)
    await tracker.status(Key)
    assert db.calls[2:] == ["2026-12-31T17:00:00+00:00", "2026-12-31T17:00:00+00:00"]  # new day and month
    assert local_periods(datetime(2026, 2, 10, 8, tzinfo=wib))[3] == datetime(2026, 3, 1, tzinfo=wib)


# ---- queue priority --------------------------------------------------------------------

async def test_priority_key_waits_longer_than_the_normal_queue_timeout(public, gw, ollama):
    ollama.post("/v1/chat/completions").respond(json=fixture_json("chat.json"))
    gw.settings.priority_queue_timeout_s = 5
    _, normal = await new_key(gw, name="normal")
    _, vip = await new_key(gw, name="vip", priority=9)
    held = hold_slots(gw)
    slow_normal = asyncio.create_task(public.post("/v1/chat/completions", headers=bearer(normal), json=CHAT))
    waiting_vip = asyncio.create_task(public.post("/v1/chat/completions", headers=bearer(vip), json=CHAT))
    await asyncio.sleep(0.6)  # past QUEUE_TIMEOUT_S (0.3 s in tests)
    held[0].close()
    r_normal, r_vip = await asyncio.gather(slow_normal, waiting_vip)
    held[1].close()
    assert r_normal.status_code == 503 and r_vip.status_code == 200
    assert scheduler_idle(gw)


async def test_priority_key_is_served_before_an_older_normal_request(public, gw, ollama):
    gw.settings.queue_timeout_s = 5
    order: list[int] = []

    def chat(request: httpx.Request) -> httpx.Response:
        order.append(json.loads(request.content)["max_tokens"])
        return httpx.Response(200, json=fixture_json("chat.json"))
    ollama.post("/v1/chat/completions").mock(side_effect=chat)
    _, normal = await new_key(gw, name="normal")
    _, vip = await new_key(gw, name="vip", priority=1)
    held = hold_slots(gw)
    first = asyncio.create_task(public.post("/v1/chat/completions", headers=bearer(normal), json={**CHAT, "max_tokens": 11}))
    await asyncio.sleep(0.05)
    second = asyncio.create_task(public.post("/v1/chat/completions", headers=bearer(vip), json={**CHAT, "max_tokens": 12}))
    while gw.scheduler.waiting < 2:
        await asyncio.sleep(0.01)
    held[0].close()  # one slot frees up: the priority request takes it, although it arrived later
    await asyncio.gather(first, second)
    held[1].close()
    assert order == [12, 11] and scheduler_idle(gw)


async def test_expiry_outside_the_supported_years_is_a_400_and_compact_dates_mean_the_whole_day(admin):
    for value in ("9999-12-31", "1960-01-01", "0001-01-01T00:00", "2150-01-01"):
        r = await admin.post("/admin/keys", json={"name": "x", "expires_at": value})
        assert r.status_code == 400 and r.json()["error"]["param"] == "expires_at", value
    a = (await admin.post("/admin/keys", json={"name": "a", "expires_at": "2099-12-31"})).json()["expires_at"]
    b = (await admin.post("/admin/keys", json={"name": "b", "expires_at": "20991231"})).json()["expires_at"]
    assert a == b

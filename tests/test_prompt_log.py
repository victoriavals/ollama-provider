"""Prompt & response audit log: capture, privacy rules, per-key opt-out, admin API filters."""

import asyncio
import json
import time

import pytest

from gateway.db import PromptRecord
from gateway.promptlog import ResponseCollector, capture_prompt
from tests.conftest import fixture_json, fixture_text, flush_usage, new_key

CHAT = {"model": "gemma3:4b", "messages": [{"role": "system", "content": "Jawab singkat."},
                                           {"role": "user", "content": "Apa ibu kota Indonesia?"}]}
PNG = "data:image/png;base64," + "A" * 40_000


def bearer(key: str, ip: str = "203.0.113.9") -> dict[str, str]:
    return {"Authorization": f"Bearer {key}", "CF-Connecting-IP": ip}


async def prompts(admin, **params) -> dict:
    r = await admin.get("/admin/prompts", params=params)
    assert r.status_code == 200, r.text
    return r.json()


async def test_chat_prompt_and_answer_are_logged_with_who_sent_them(public, admin, gw, ollama):
    ollama.post("/v1/chat/completions").respond(json=fixture_json("chat.json"))
    _, key = await new_key(gw, name="teman-a")
    r = await public.post("/v1/chat/completions", headers=bearer(key), json={**CHAT, "user": "budi@contoh"})
    assert r.status_code == 200
    await flush_usage(gw)
    [row] = (await prompts(admin))["data"]
    assert (row["key_name"], row["client_ip"], row["end_user"], row["model"], row["status_code"]) == (
        "teman-a", "203.0.113.9", "budi@contoh", "gemma3:4b", 200)
    assert row["prompt_preview"] == "Apa ibu kota Indonesia?" and row["response_preview"] == "Hello, how are you?"
    assert row["total_tokens"] == 23 and "prompt" not in row  # the list carries previews only
    full = (await admin.get(f"/admin/prompts/{row['id']}")).json()
    assert full["prompt"] == [{"role": "system", "content": "Jawab singkat."},
                              {"role": "user", "content": "Apa ibu kota Indonesia?"}]
    assert full["response"] == "Hello, how are you?" and full["request_id"] == r.headers["x-request-id"]


async def test_stream_answer_is_collected_from_chunks(public, admin, gw, ollama):
    ollama.post("/v1/chat/completions").respond(200, content=fixture_text("chat_stream.txt").encode())
    _, key = await new_key(gw)
    await public.post("/v1/chat/completions", headers=bearer(key), json={**CHAT, "stream": True})
    await flush_usage(gw)
    [row] = (await prompts(admin))["data"]
    full = (await admin.get(f"/admin/prompts/{row['id']}")).json()
    assert (full["response"], full["stream"], full["total_tokens"]) == ("Hello there, friend!", 1, 22)


async def test_images_are_not_stored_and_secrets_are_masked(public, admin, gw, ollama):
    ollama.post("/v1/chat/completions").respond(json=fixture_json("chat.json"))
    gw.settings.max_body_bytes = 200_000
    _, key = await new_key(gw)
    msg = {"role": "user", "content": [{"type": "text", "text": "Ini key saya sk-abcdefghijklmnop, apa ini?"},
                                       {"type": "image_url", "image_url": {"url": PNG}}]}
    await public.post("/v1/chat/completions", headers=bearer(key), json={"model": "gemma3:4b", "messages": [msg]})
    await flush_usage(gw)
    [row] = (await prompts(admin))["data"]
    stored = json.dumps((await admin.get(f"/admin/prompts/{row['id']}")).json())
    assert "AAAA" not in stored and "[gambar ~29 KB]" in stored
    assert "sk-abcdefghijklmnop" not in stored and "sk-***" in stored


async def test_long_text_is_cut_to_the_configured_size():
    messages, preview, chars = capture_prompt({"messages": [{"role": "user", "content": "x" * 100}]}, "chat", 30)
    assert chars == 100 and messages[0]["content"].startswith("x" * 30)
    assert messages[0]["content"].endswith("[dipotong 70 karakter]") and len(preview) == 100
    collector = ResponseCollector(10)
    collector.add_json({"choices": [{"message": {"content": "y" * 25}}]})
    assert collector.text() == "y" * 10 + "… [dipotong 15 karakter]"


async def test_stream_tool_calls_are_reassembled():
    c = ResponseCollector(1000)
    c.add_chunk({"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"name": "cek_cuaca", "arguments": ""}}]}}]})
    c.add_chunk({"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": '{"kota":"Bandung"}'}}]}}]})
    assert c.text() == '[tool call] cek_cuaca({"kota":"Bandung"})'


async def test_key_can_opt_out_and_logging_can_be_disabled(public, admin, gw, ollama):
    ollama.post("/v1/chat/completions").respond(json=fixture_json("chat.json"))
    _, quiet = await new_key(gw, name="pribadi", log_prompts=False)
    await public.post("/v1/chat/completions", headers=bearer(quiet), json=CHAT)
    gw.settings.prompt_log_enabled = False
    _, normal = await new_key(gw, name="biasa")
    await public.post("/v1/chat/completions", headers=bearer(normal), json=CHAT)
    await flush_usage(gw)
    assert (await prompts(admin))["data"] == []


async def test_rejected_requests_are_logged_with_their_status(public, admin, gw, ollama):
    _, key = await new_key(gw, allowed_models=["gemma3:4b"])
    r = await public.post("/v1/embeddings", headers=bearer(key), json={"model": "nomic-embed-text", "input": "rahasia?"})
    assert r.status_code == 404
    await flush_usage(gw)
    [row] = (await prompts(admin, status="error"))["data"]
    assert (row["status_code"], row["prompt_preview"], row["response_preview"]) == (404, "rahasia?", None)


async def test_embeddings_are_logged_without_vectors(public, admin, gw, ollama):
    ollama.post("/v1/embeddings").respond(json={"object": "list", "data": [{"embedding": [0.1]}, {"embedding": [0.2]}],
                                                "usage": {"prompt_tokens": 2, "total_tokens": 2}})
    _, key = await new_key(gw)
    await public.post("/v1/embeddings", headers=bearer(key), json={"model": "nomic-embed-text", "input": ["a", "b"]})
    await flush_usage(gw)
    [row] = (await prompts(admin))["data"]
    assert (row["prompt_preview"], row["response_preview"]) == ("a b", "[2 embedding]")


async def _seed(gw, n: int, **fields) -> None:
    base = {"key_id": 1, "key_name": "k", "endpoint": "chat", "model": "gemma3:4b", "prompt": [], "prompt_chars": 0}
    await gw.db.insert_prompts([PromptRecord(**{**base, "prompt_preview": f"p{i}", **fields}) for i in range(n)])


async def test_admin_filters_search_and_pages(admin, gw):
    await _seed(gw, 5, key_id=1, response="jawaban biasa")
    await _seed(gw, 2, key_id=2, status_code=429, response=None, prompt=[{"role": "user", "content": "cari 100%_x"}])
    await _seed(gw, 1, key_id=1, ts="2020-01-01T00:00:00+00:00")  # outside the default 24 h window
    assert len((await prompts(admin))["data"]) == 7
    assert len((await prompts(admin, since="2019-12-31T00:00:00+00:00"))["data"]) == 8
    assert {r["key_id"] for r in (await prompts(admin, key_id=2))["data"]} == {2}
    assert len((await prompts(admin, status="error"))["data"]) == 2
    assert len((await prompts(admin, q="100%_"))["data"]) == 2  # % and _ are literal, not wildcards
    assert len((await prompts(admin, q="0%x"))["data"]) == 0
    assert (await admin.get("/admin/prompts", params={"q": "ab"})).status_code == 400
    page1 = await prompts(admin, limit=4)
    page2 = await prompts(admin, limit=4, before=page1["next"])
    ids = [r["id"] for r in page1["data"] + page2["data"]]
    assert len(ids) == 7 == len(set(ids)) and page2["next"] is None
    assert (await admin.get("/admin/prompts/999")).status_code == 404


async def test_prompt_list_queries_stay_on_the_ts_index(gw, monkeypatch):
    """Every dashboard query must be an index range scan without a temp sort (fast at any table size)."""
    captured = []
    real_all = gw.db._read_all

    async def spy(sql, params=()):
        captured.append((sql, params))
        return await real_all(sql, params)
    monkeypatch.setattr(gw.db, "_read_all", spy)
    since = "2026-01-01T00:00:00+00:00"
    for kwargs in ({}, {"before": ("2026-06-01T00:00:00+00:00", 10)}, {"key_id": 1}, {"failed": True},
                   {"query": "abc", "before": ("2026-06-01T00:00:00+00:00", 10)}):
        await gw.db.list_prompts(since, **kwargs)
    for sql, params in captured:
        plan = " ".join(r["detail"] for r in await real_all("EXPLAIN QUERY PLAN " + sql, params))
        assert "TEMP B-TREE" not in plan and "idx_prompt_" in plan, (sql, plan)


async def test_prompt_reads_use_the_read_only_connection(gw):
    """A slow dashboard search must not hold the main connection that API requests depend on."""
    await _seed(gw, 1)
    assert gw.db._reader is not None and gw.db._reader is not gw.db._conn
    blocked = asyncio.Event()

    def slow(*_):  # SQLite user function: runs inside the search, on the reader's thread
        blocked.set()
        time.sleep(0.5)
        return 1
    await gw.db._reader.create_function("slow", 1, slow)
    search = asyncio.create_task(gw.db._read_all("SELECT slow(id) FROM prompt_logs"))
    await asyncio.wait_for(blocked.wait(), 2)
    t = time.perf_counter()
    assert await gw.db.is_model_enabled("gemma3:4b")  # main connection answers while the search runs
    assert time.perf_counter() - t < 0.25
    await search
    with pytest.raises(Exception):  # and the reader really is read-only
        await gw.db._reader.execute("DELETE FROM prompt_logs")


async def test_old_prompts_are_pruned(gw):
    await _seed(gw, 1, ts="2020-01-01T00:00:00+00:00")
    await _seed(gw, 1)
    await gw.db.prune_prompts(30)  # the gateway's own prune loop may already have run as well
    rows = await gw.db.list_prompts("2000-01-01T00:00:00+00:00")
    assert len(rows) == 1 and rows[0]["ts"] > "2020-01-02"


async def test_admin_sets_prompt_logging_per_key(admin):
    k = (await admin.post("/admin/keys", json={"name": "pribadi", "log_prompts": False})).json()
    assert k["log_prompts"] == 0
    k = (await admin.patch(f"/admin/keys/{k['id']}", json={"log_prompts": None})).json()
    assert k["log_prompts"] == 1


# ---- QA regressions (review 2026-11-03) ------------------------------------------------------

async def test_log_stores_exactly_what_the_model_receives(public, admin, gw, ollama):
    route = ollama.post("/v1/chat/completions").respond(json=fixture_json("chat.json"))
    _, key = await new_key(gw)
    body = {"model": "gemma3:4b", "messages": [{"role": "user", "content": "innocent", "Content": "hidden prompt"}]}
    await public.post("/v1/chat/completions", headers=bearer(key), json=body)
    await flush_usage(gw)
    sent = json.loads(route.calls.last.request.content)["messages"]
    assert sent == [{"role": "user", "content": "innocent"}]  # the variant key never reaches Ollama
    [row] = (await prompts(admin))["data"]
    full = (await admin.get(f"/admin/prompts/{row['id']}")).json()
    assert full["prompt"] == sent and "hidden" not in json.dumps(full)


async def test_aborted_stream_is_estimated_and_counted_towards_the_quota(gw, ollama):
    from gateway.proxy import RequestContext, stream_sse
    ollama.post("/v1/chat/completions").respond(200, content=fixture_text("chat_stream.txt").encode())
    key_id, api_key = await new_key(gw, daily_token_limit=1000)
    key = await gw.lookup_key(api_key)
    await gw.quota.status(key)  # start tracking this key
    assert gw.gate.try_acquire_key(key.id, 2)
    ctx = RequestContext(key=key, endpoint="chat", model="gemma3:4b", request_id="req_x", client_ip="1.1.1.1",
                         prompt_tokens_est=12)
    resp = stream_sse(gw, "/v1/chat/completions", {**CHAT, "stream": True}, ctx, include_usage=False)
    it = resp.body_iterator
    assert await it.__anext__() == ": connected\n\n"
    await it.__anext__()
    await it.__anext__()  # two content chunks, then the client leaves
    await it.aclose()
    [row] = await flush_usage(gw)
    assert row["status_code"] == 499 and (row["prompt_tokens"], row["completion_tokens"], row["total_tokens"]) == (12, 2, 14)
    assert (await gw.quota.status(key)).day_used == 14


async def test_capture_failure_still_leaves_an_audit_row(public, admin, gw, ollama, monkeypatch):
    ollama.post("/v1/chat/completions").respond(json=fixture_json("chat.json"))

    def boom(*args, **kwargs):
        raise RuntimeError("capture bug")
    monkeypatch.setattr("gateway.apps.public.new_record", boom)
    monkeypatch.setattr("gateway.apps.public.capture_prompt", boom)
    _, key = await new_key(gw, name="aneh")
    assert (await public.post("/v1/chat/completions", headers=bearer(key), json=CHAT)).status_code == 200
    await flush_usage(gw)
    [row] = (await prompts(admin))["data"]
    assert row["key_name"] == "aneh" and "tidak dapat dibaca" in row["prompt_preview"]


async def test_malformed_upstream_answer_does_not_break_the_response(public, admin, gw, ollama):
    weird = {"id": "x", "object": "chat.completion", "choices": [None, {"message": None}, {"message": {"tool_calls": [None, 5]}}],
             "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}
    ollama.post("/v1/chat/completions").respond(json=weird)
    _, key = await new_key(gw)
    r = await public.post("/v1/chat/completions", headers=bearer(key), json=CHAT)
    assert r.status_code == 200
    stream = b'data: {"choices":[null,{"delta":null},{"delta":{"tool_calls":[null,{"index":0}]}}]}\n\ndata: [DONE]\n\n'
    ollama.post("/v1/chat/completions").respond(200, content=stream)
    r = await public.post("/v1/chat/completions", headers=bearer(key), json={**CHAT, "stream": True})
    assert r.status_code == 200 and "[DONE]" in r.text
    await flush_usage(gw)
    assert len((await prompts(admin))["data"]) == 2


async def test_separate_stream_tool_calls_with_the_same_index_stay_apart():
    c = ResponseCollector(1000)
    c.add_chunk({"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "a", "function": {"name": "f1", "arguments": "{}"}}]}}]})
    c.add_chunk({"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "b", "function": {"name": "f2", "arguments": '{"x":1}'}}]}}]})
    assert c.text() == '[tool call] f1({})\n[tool call] f2({"x":1})'


async def test_search_finds_text_with_quotes_and_newlines(admin, gw):
    await _seed(gw, 1, prompt=[{"role": "user", "content": 'kata "kunci"\nbaris dua'}])
    assert len((await prompts(admin, q='"kunci"'))["data"]) == 1
    assert len((await prompts(admin, q="kunci\"\nbaris"))["data"]) == 1


def test_prompt_log_settings_are_validated():
    from gateway.config import Settings
    for field in ("prompt_log_max_chars", "prompt_log_retention_days"):
        with pytest.raises(ValueError):
            Settings(_env_file=None, **{field: 0})

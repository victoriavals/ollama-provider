import json

import httpx

from gateway.proxy import RequestContext, stream_sse
from tests.conftest import fixture_json, fixture_text, flush_usage, hold_slots, new_key, scheduler_idle

CHAT = {"model": "gemma3:4b", "messages": [{"role": "user", "content": "Say hi in 3 words"}]}


def sse_data(text: str) -> list[str]:
    return [line[6:] for line in text.splitlines() if line.startswith("data: ")]


def mock_chat_stream(ollama):
    return ollama.post("/v1/chat/completions").respond(
        200, content=fixture_text("chat_stream.txt").encode(), headers={"Content-Type": "text/event-stream"})


async def test_non_stream_chat_forwards_sanitized_payload_and_logs_usage(public, auth, gw, ollama):
    route = ollama.post("/v1/chat/completions").respond(json=fixture_json("chat.json"))
    r = await public.post("/v1/chat/completions", headers=auth,
                          json={**CHAT, "options": {"num_ctx": 999999}, "keep_alive": -1, "max_tokens": 5000})
    assert r.status_code == 200
    assert r.json()["choices"][0]["message"]["content"] == "Hello, how are you?"
    assert r.headers["x-ratelimit-limit-requests"] == "100"

    sent = json.loads(route.calls.last.request.content)
    assert "options" not in sent and "keep_alive" not in sent
    assert sent["max_tokens"] == gw.settings.max_tokens_cap and sent["stream"] is False

    [row] = await flush_usage(gw)
    assert (row["endpoint"], row["status_code"], row["stream"]) == ("chat", 200, 0)
    assert (row["prompt_tokens"], row["completion_tokens"], row["total_tokens"]) == (16, 7, 23)


async def test_stream_hides_usage_chunk_unless_requested(public, auth, gw, ollama):
    mock_chat_stream(ollama)
    r = await public.post("/v1/chat/completions", headers=auth, json={**CHAT, "stream": True})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    assert r.headers["cache-control"] == "no-cache"
    data = sse_data(r.text)
    assert data[-1] == "[DONE]"
    chunks = [json.loads(d) for d in data[:-1]]
    assert "".join(c["choices"][0]["delta"].get("content", "") for c in chunks) == "Hello there, friend!"
    assert all(c["choices"] for c in chunks)  # usage-only chunk stripped

    [row] = await flush_usage(gw)
    assert (row["stream"], row["status_code"], row["total_tokens"]) == (1, 200, 22)


async def test_stream_includes_usage_chunk_when_requested(public, auth, ollama):
    route = mock_chat_stream(ollama)
    r = await public.post("/v1/chat/completions", headers=auth,
                          json={**CHAT, "stream": True, "stream_options": {"include_usage": True}})
    last = json.loads(sse_data(r.text)[-2])
    assert last["choices"] == [] and last["usage"]["total_tokens"] == 22
    assert json.loads(route.calls.last.request.content)["stream_options"] == {"include_usage": True}


async def test_completions_stream(public, auth, gw, ollama):
    ollama.post("/v1/completions").respond(200, content=fixture_text("completions_stream.txt").encode(),
                                           headers={"Content-Type": "text/event-stream"})
    r = await public.post("/v1/completions", headers=auth,
                          json={"model": "gemma3:4b", "prompt": "The sky is", "stream": True})
    text = "".join(json.loads(d)["choices"][0]["text"] for d in sse_data(r.text)[:-1])
    assert text == "The sky is **blue"
    [row] = await flush_usage(gw)
    assert (row["prompt_tokens"], row["completion_tokens"]) == (13, 5)


async def test_unknown_and_disabled_models_are_404(public, auth, gw):
    r = await public.post("/v1/chat/completions", headers=auth, json={**CHAT, "model": "llama3:70b"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "model_not_found"
    await gw.db.set_model_enabled("gemma3:4b", False)
    r = await public.post("/v1/chat/completions", headers=auth, json=CHAT)
    assert r.status_code == 404


async def test_embeddings(public, auth, ollama):
    r = await public.post("/v1/embeddings", headers=auth, json={"model": "gemma3:4b", "input": "hi"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "model_not_supported"

    ollama.post("/v1/embeddings").respond(json={
        "object": "list", "data": [{"object": "embedding", "index": 0, "embedding": [0.1, 0.2]}],
        "model": "nomic-embed-text", "usage": {"prompt_tokens": 2, "total_tokens": 2}})
    r = await public.post("/v1/embeddings", headers=auth, json={"model": "nomic-embed-text", "input": "hi"})
    assert r.status_code == 200 and r.json()["data"][0]["embedding"] == [0.1, 0.2]


async def test_body_too_large_is_413(public, auth):
    big = {**CHAT, "messages": [{"role": "user", "content": "x" * 20_000}]}
    r = await public.post("/v1/chat/completions", headers=auth, json=big)
    assert r.status_code == 413


async def test_invalid_json_is_400(public, auth):
    r = await public.post("/v1/chat/completions", headers={**auth, "Content-Type": "application/json"},
                          content=b"{bad")
    assert r.status_code == 400 and r.json()["error"]["type"] == "invalid_request_error"


async def test_rpm_limit_returns_429_with_retry_after(public, gw, ollama):
    ollama.post("/v1/chat/completions").respond(json=fixture_json("chat.json"))
    _, key = await new_key(gw, rpm_limit=2)
    headers = {"Authorization": f"Bearer {key}"}
    assert [(await public.post("/v1/chat/completions", headers=headers, json=CHAT)).status_code
            for _ in range(2)] == [200, 200]
    r = await public.post("/v1/chat/completions", headers=headers, json=CHAT)
    assert r.status_code == 429 and r.json()["error"]["code"] == "rate_limit_exceeded"
    assert int(r.headers["Retry-After"]) > 0
    rows = await flush_usage(gw)
    assert [row["status_code"] for row in rows] == [200, 200, 429]


async def test_per_key_concurrency_limit(public, auth, gw, api_key):
    key = await gw.lookup_key(api_key)
    assert gw.gate.try_acquire_key(key.id, 2) and gw.gate.try_acquire_key(key.id, 2)
    r = await public.post("/v1/chat/completions", headers=auth, json=CHAT)
    assert r.status_code == 429 and r.json()["error"]["code"] == "concurrency_limit_exceeded"


async def test_server_busy_when_all_gpu_slots_taken(public, auth, gw):
    held = hold_slots(gw)
    r = await public.post("/v1/chat/completions", headers=auth, json=CHAT)
    assert r.status_code == 503 and r.json()["error"]["code"] == "server_busy"
    assert "Retry-After" in r.headers

    r = await public.post("/v1/chat/completions", headers=auth, json={**CHAT, "stream": True})
    assert r.status_code == 200  # headers already sent; error arrives as an SSE event
    assert json.loads(sse_data(r.text)[-1])["error"]["code"] == "server_busy"
    assert ": ping" in r.text
    for ticket in held:
        ticket.close()
    assert scheduler_idle(gw)


async def test_upstream_down_is_503(public, auth, gw, ollama):
    ollama.post("/v1/chat/completions").mock(side_effect=httpx.ConnectError("refused"))
    r = await public.post("/v1/chat/completions", headers=auth, json=CHAT)
    assert r.status_code == 503 and r.json()["error"]["code"] == "upstream_unavailable"
    r = await public.post("/v1/chat/completions", headers=auth, json={**CHAT, "stream": True})
    assert json.loads(sse_data(r.text)[-1])["error"]["code"] == "upstream_unavailable"
    assert scheduler_idle(gw)


async def test_upstream_error_status_passes_through(public, auth, ollama):
    ollama.post("/v1/chat/completions").respond(
        500, json={"error": {"message": "model runner has unexpectedly stopped", "type": "api_error"}})
    r = await public.post("/v1/chat/completions", headers=auth, json=CHAT)
    assert r.status_code == 500 and "unexpectedly stopped" in r.json()["error"]["message"]


async def test_client_disconnect_mid_stream_releases_slots_and_logs_499(gw, ollama, api_key):
    mock_chat_stream(ollama)
    key = await gw.lookup_key(api_key)
    assert gw.gate.try_acquire_key(key.id, 2)
    ctx = RequestContext(key=key, endpoint="chat", model="gemma3:4b", request_id="req_t", client_ip="1.1.1.1")
    payload = {**CHAT, "stream": True, "stream_options": {"include_usage": True}, "max_tokens": 10}
    resp = stream_sse(gw, "/v1/chat/completions", payload, ctx, include_usage=False)

    it = resp.body_iterator
    assert await it.__anext__() == ": connected\n\n"
    assert (await it.__anext__()).startswith("data: ")
    assert gw.scheduler.active == 1
    await it.aclose()  # what ClosingStreamingResponse does when the client goes away

    assert scheduler_idle(gw)
    [row] = await flush_usage(gw)
    assert row["status_code"] == 499 and row["stream"] == 1


async def test_stream_from_ollama_040_with_timings(public, auth, gw, ollama):
    # Ollama 0.40 adds a `timings` object to stream chunks; the usage chunk and [DONE] are unchanged.
    ollama.post("/v1/chat/completions").respond(
        200, content=fixture_text("chat_stream_ollama040.txt").encode(), headers={"Content-Type": "text/event-stream"})
    r = await public.post("/v1/chat/completions", headers=auth, json={**CHAT, "stream": True})
    data = sse_data(r.text)
    assert data[-1] == "[DONE]"
    chunks = [json.loads(d) for d in data[:-1]]
    assert chunks and all(c["choices"] for c in chunks)  # usage-only chunk stripped
    [row] = await flush_usage(gw)
    assert row["status_code"] == 200 and row["total_tokens"] and row["total_tokens"] > 0

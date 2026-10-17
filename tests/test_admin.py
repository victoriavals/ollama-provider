import asyncio
import json

import httpx

from gateway.apps.admin import create_admin_app
from tests.conftest import ADMIN_KEY, fixture_json


async def test_admin_requires_admin_key(admin):
    assert (await admin.get("/admin/keys", headers={"Authorization": ""})).status_code == 401
    assert (await admin.get("/admin/keys", headers={"Authorization": "Bearer nope"})).status_code == 401
    assert (await admin.get("/admin/keys")).status_code == 200


async def test_admin_disabled_without_admin_key(admin, gw):
    gw.settings.admin_key = ""
    r = await admin.get("/admin/keys")
    assert r.status_code == 503 and r.json()["error"]["code"] == "admin_disabled"
    gw.settings.admin_key = ADMIN_KEY


async def test_key_lifecycle(admin, public, ollama):
    ollama.post("/v1/chat/completions").respond(json=fixture_json("chat.json"))
    r = await admin.post("/admin/keys", json={"name": "client-a", "rpm_limit": 30})
    assert r.status_code == 201
    created = r.json()
    key = created["key"]
    assert key.startswith("sk-") and created["prefix"] == key[:10]
    assert created["rpm_limit_effective"] == 30 and created["max_concurrent_effective"] == 2

    listed = (await admin.get("/admin/keys")).json()["data"]
    assert len(listed) == 1 and "key" not in listed[0] and "key_hash" not in listed[0]

    headers = {"Authorization": f"Bearer {key}"}
    chat = {"model": "gemma3:4b", "messages": [{"role": "user", "content": "hi"}]}
    r = await public.post("/v1/chat/completions", headers=headers, json=chat)
    assert r.status_code == 200 and r.headers["x-ratelimit-limit-requests"] == "30"

    r = await admin.patch(f"/admin/keys/{created['id']}", json={"rpm_limit": None, "max_concurrent": 4})
    assert r.json()["rpm_limit"] is None and r.json()["rpm_limit_effective"] == 100
    assert r.json()["max_concurrent"] == 4

    assert (await admin.delete(f"/admin/keys/{created['id']}")).json() == {"id": created["id"], "revoked": True}
    assert (await admin.delete(f"/admin/keys/{created['id']}")).status_code == 404
    assert (await public.post("/v1/chat/completions", headers=headers, json=chat)).status_code == 401


async def test_key_validation(admin):
    assert (await admin.post("/admin/keys", json={"name": ""})).status_code == 400
    assert (await admin.post("/admin/keys", json={"name": "x", "rpm_limit": 0})).status_code == 400
    assert (await admin.patch("/admin/keys/999", json={"rpm_limit": 5})).status_code == 404


async def test_usage_summary(admin, public, auth, gw, ollama):
    ollama.post("/v1/chat/completions").respond(json=fixture_json("chat.json"))
    chat = {"model": "gemma3:4b", "messages": [{"role": "user", "content": "hi"}]}
    for _ in range(3):
        await public.post("/v1/chat/completions", headers=auth, json=chat)
    await public.post("/v1/chat/completions", headers=auth, json={**chat, "model": "missing"})
    await gw.usage.stop()
    gw.usage.start()

    by_model = (await admin.get("/admin/usage", params={"group_by": "model"})).json()["data"]
    gemma = next(r for r in by_model if r["model"] == "gemma3:4b")
    assert gemma["requests"] == 3 and gemma["total_tokens"] == 69 and gemma["errors"] == 0
    by_key = (await admin.get("/admin/usage", params={"group_by": "key"})).json()["data"]
    assert by_key[0]["requests"] == 4 and by_key[0]["errors"] == 1 and by_key[0]["key_name"] == "test"
    recent = (await admin.get("/admin/usage/recent", params={"limit": 2})).json()["data"]
    assert len(recent) == 2
    assert (await admin.get("/admin/usage", params={"since": "yesterday"})).status_code == 400


async def test_models_list_enable_disable(admin, public, auth):
    data = {m["name"]: m for m in (await admin.get("/admin/models")).json()["data"]}
    assert data["gemma3:4b"]["installed"] and data["gemma3:4b"]["enabled"]
    assert data["gemma3:4b"]["parameter_size"] == "4.3B"

    assert (await admin.patch("/admin/models/qwen3:8b", json={"enabled": True})).status_code == 404
    assert (await admin.patch("/admin/models/nomic-embed-text", json={"enabled": False})).status_code == 200
    ids = [m["id"] for m in (await public.get("/v1/models", headers=auth)).json()["data"]]
    assert ids == ["gemma3:4b"]


async def test_pull_job_runs_in_background(admin, gw, ollama):
    events = [{"status": "pulling manifest"},
              {"status": "pulling abc", "total": 200, "completed": 100},
              {"status": "pulling abc", "total": 200, "completed": 200},
              {"status": "success"}]
    ollama.post("/api/pull").respond(200, content="\n".join(json.dumps(e) for e in events).encode())
    r = await admin.post("/admin/models/pull", json={"model": "qwen3:8b", "enable": True})
    assert r.status_code == 202
    job_id = r.json()["id"]
    for _ in range(50):
        job = (await admin.get(f"/admin/models/pull/{job_id}")).json()
        if job["status"] != "running":
            break
        await asyncio.sleep(0.02)
    assert job["status"] == "success" and job["percent"] == 100.0
    assert await gw.db.is_model_enabled("qwen3:8b")


async def test_pull_job_reports_errors(admin, ollama):
    ollama.post("/api/pull").respond(200, content=b'{"error":"pull model manifest: file does not exist"}')
    job_id = (await admin.post("/admin/models/pull", json={"model": "nope:1b"})).json()["id"]
    for _ in range(50):
        job = (await admin.get(f"/admin/models/pull/{job_id}")).json()
        if job["status"] != "running":
            break
        await asyncio.sleep(0.02)
    assert job["status"] == "error" and "does not exist" in job["error"]


async def test_delete_model(admin, gw, ollama):
    ollama.delete("/api/delete").respond(200)
    r = await admin.delete("/admin/models/nomic-embed-text")
    assert r.json() == {"name": "nomic-embed-text", "deleted": True}
    assert not await gw.db.is_model_enabled("nomic-embed-text")


async def test_status(admin, ollama):
    body = (await admin.get("/admin/status")).json()
    assert body["ollama"] == {"reachable": True, "version": "0.34.0", "loaded_models": []}
    assert body["gateway"]["concurrency"]["global_limit"] == 2

    ollama.get("/api/version").mock(side_effect=httpx.ConnectError("down"))
    body = (await admin.get("/admin/status")).json()
    assert body["ollama"]["reachable"] is False


async def test_dashboard_is_served_with_strict_headers(admin):
    r = await admin.get("/admin/ui", headers={"Authorization": ""})
    assert r.status_code == 200 and "Kontrol Ollama Provider" in r.text
    csp = r.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp and "unsafe-inline" not in csp
    assert r.headers["x-frame-options"] == "DENY" and r.headers["cache-control"] == "no-store"
    js = await admin.get("/admin/ui/app.js")
    assert js.status_code == 200 and js.headers["content-type"].startswith("text/javascript")
    assert ".innerHTML" not in js.text and "insertAdjacentHTML" not in js.text  # text via textContent only
    assert (await admin.get("/admin/ui/../../.env")).status_code == 404
    assert (await admin.get("/admin/ui/index.html")).status_code == 404
    assert (await admin.get("/", follow_redirects=False)).headers["location"] == "/admin/ui"


async def test_admin_rejects_foreign_host_header(gw):
    # DNS rebinding: a web page on evil.example resolving to 127.0.0.1 must not reach the admin app.
    transport = httpx.ASGITransport(app=create_admin_app(gw))
    async with httpx.AsyncClient(transport=transport, base_url="http://evil.example:8001") as client:
        assert (await client.get("/admin/ui")).status_code == 400
        assert (await client.get("/admin/keys", headers={"Authorization": f"Bearer {ADMIN_KEY}"})).status_code == 400


async def test_info_and_hourly_usage(admin, public, auth, gw, ollama):
    gw.settings.public_base_url = "https://example.test/v1"
    info = (await admin.get("/admin/info")).json()
    assert info["public_base_url"] == "https://example.test/v1"
    assert info["defaults"]["rpm"] == 100 and "gemma3:4b" in info["models"]

    ollama.post("/v1/chat/completions").respond(json=fixture_json("chat.json"))
    await public.post("/v1/chat/completions", headers=auth,
                      json={"model": "gemma3:4b", "messages": [{"role": "user", "content": "hi"}]})
    await gw.usage.stop()
    gw.usage.start()
    [row] = (await admin.get("/admin/usage", params={"group_by": "hour"})).json()["data"]
    assert len(row["hour"]) == 13 and row["requests"] == 1

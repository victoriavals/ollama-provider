"""Loopback-only dashboard demo. All data and mutations live in memory; no Ollama or DB."""

import argparse
import asyncio
import copy
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from gateway.dashboard import ui
from gateway.errors import install_handlers

DEMO_KEY = "demo-admin"  # Public demo credential, never a production credential.


def initial_state():
    now = datetime.now(timezone.utc)
    keys = []
    for index, name in enumerate(["aplikasi-chat", "asisten-internal", "integrasi-tools"], 1):
        keys.append(dict(id=index, name=name, prefix=f"sk-demo{index}", created_at=now.isoformat(),
                         rpm_limit=60, max_concurrent=2, rpm_limit_effective=60, max_concurrent_effective=2,
                         daily_token_limit=100000, monthly_token_limit=2000000, expires_at=None,
                         allowed_models=None, priority=1 if index == 1 else 0, log_prompts=1,
                         usage={"day_tokens": index * 14000, "month_tokens": index * 154000},
                         expired=False, revoked_at=None, last_used_at=now.isoformat()))
    requests = [dict(id=index, ts=(now - timedelta(minutes=index)).isoformat(), key_id=index % 3 + 1,
                     model="gemma3:4b" if index % 4 else "qwen3.5:4b", status_code=429 if index % 6 == 0 else 200,
                     total_tokens=120 + index * 7, prompt_tokens=50, completion_tokens=70,
                     latency_ms=1400 + index * 130, client_ip=f"192.0.2.{index + 1}",
                     endpoint="/v1/chat/completions", stream=True) for index in range(24)]
    prompts = [dict(**{**requests[index % len(requests)], "id": 120 - index,
                       "ts": (now - timedelta(minutes=index)).isoformat()},
                    key_name=keys[index % 3]["name"], end_user="demo-user",
                    prompt_preview="Jelaskan cara kerja antrean model dan penggunaan token untuk aplikasi.",
                    response_preview="Antrean model mengatur request sesuai kapasitas GPU dan prioritas client.",
                    prompt=[{"role": "user", "content": "Jelaskan cara kerja antrean model."}],
                    response="Antrean mengatur akses sesuai kapasitas GPU. Setiap client mendapat giliran.",
                    request_id=f"demo-request-{index}") for index in range(120)]
    return dict(keys=keys, requests=requests, prompts=prompts, jobs=[],
                models=[dict(name=name, installed=True, enabled=True, size_bytes=3 * 2**30,
                             parameter_size="4B", quantization="Q4_K_M") for name in ["gemma3:4b", "qwen3.5:4b"]],
                failures={}, counts={}, delay=0, empty=False)


app = FastAPI(docs_url=None, openapi_url=None)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])
app.include_router(ui)
install_handlers(app)
state = initial_state()


@app.api_route("/_test/{action}", methods=["GET", "POST"])
async def control(action: str, request: Request):
    global state
    if action == "reset":
        state = initial_state()
    elif action == "configure":
        body = await request.json()
        for key in ("failures", "delay", "empty"):
            if key in body:
                state[key] = body[key]
        if body.get("inject"):
            state["keys"][0]["name"] = '<img src=x onerror="window.__xss=1">'
            state["prompts"][0]["prompt"][0]["content"] = '<script>window.__xss=1</script>'
            state["prompts"][0]["response"] = '<img src=x onerror="window.__xss=1">'
        if body.get("edge_keys"):
            state["keys"][0].update(name="client-" + "x" * 90, expired=True, expires_at="2001-01-01T17:00:00+00:00")
            state["keys"][1]["usage"]["day_tokens"] = 100001
            state["models"][0]["name"] = "model-" + "x" * 190
    return {"counts": state["counts"], "keys": state["keys"] if action == "inspect" else [], "models": state["models"] if action == "inspect" else [], "last_patch": state.get("last_patch")}


def error(status: int, message: str):
    return JSONResponse({"error": {"message": message}}, status_code=status)


@app.api_route("/admin/{resource:path}", methods=["GET", "POST", "PATCH", "DELETE"])
async def api(resource: str, request: Request):
    if request.headers.get("authorization") != f"Bearer {DEMO_KEY}":
        return error(401, "Invalid admin key.")
    method = request.method
    route = f"{method} /{resource}"
    state["counts"][route] = state["counts"].get(route, 0) + 1
    if state["delay"]:
        await asyncio.sleep(state["delay"] / 1000)
    failure = state["failures"].get(resource)
    if failure:
        if failure == 422:
            return JSONResponse({"detail": [{"loc": ["body", "rpm_limit"], "msg": "Nilai harus minimal 1.", "type": "greater_than_equal"}]}, status_code=422)
        return error(failure, "Simulasi koneksi server gagal.")
    now = datetime.now(timezone.utc)
    params = request.query_params
    if resource == "info":
        return dict(demo_mode=True, public_base_url="https://api.example.test/v1", client_guide_url="https://example.test/guide",
                    defaults=dict(rpm=60, max_concurrent=2, global_concurrency=4, max_tokens_cap=2048),
                    models=[model["name"] for model in state["models"] if model["enabled"]])
    if resource == "status":
        components = {key: {"status": "ok", "detail": detail} for key, detail in [
            ("ollama", "version 0.40.5"), ("model_gpu", "gemma3:4b, 100% GPU"),
            ("gpu", "NVIDIA RTX 4060 Ti, 52 C, 6.1/8 GB VRAM"),
            ("service:OllamaServe", "running"), ("service:NgrokTunnel", "running"), ("disk:D:", "120 GB free")
        ]}
        concurrency = dict(global_limit=4, active=3, waiting=6, max_queue=32, switch_after_s=10,
                           turn={"model": "gemma3:4b", "yielding": False, "age_s": 6},
                           lanes=[dict(model="gemma3:4b", capacity=4, running=3, waiting=4, oldest_wait_s=3.6),
                                  dict(model="qwen3.5:4b", capacity=1, running=0, waiting=2, oldest_wait_s=7.3)],
                           stats={"switches": 12, "expired": 2, "rejected": 0})
        return dict(ts=now.isoformat(), components=components, gateway=dict(concurrency=concurrency, uptime_s=34000, pull_jobs_running=0))
    if resource == "keys" and method == "GET":
        local = now.astimezone(timezone(timedelta(hours=7)))
        day = local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        month = (local.replace(day=28) + timedelta(days=4)).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return {"data": [] if state["empty"] else state["keys"], "quota_resets": {"day": day.isoformat(), "month": month.isoformat()}}
    if resource == "keys" and method == "POST":
        body = await request.json()
        row = copy.deepcopy(initial_state()["keys"][0])
        row.update(body, id=max(key["id"] for key in state["keys"]) + 1, prefix="sk-demo-new")
        row["rpm_limit_effective"] = row["rpm_limit"] or 60
        row["max_concurrent_effective"] = row["max_concurrent"] or 2
        row["usage"] = {"day_tokens": 0, "month_tokens": 0}
        row["log_prompts"] = int(bool(row["log_prompts"]))
        state["keys"].append(row)
        return JSONResponse({**row, "key": "sk-demo-new-secret", "note": "Demo only"}, status_code=201)
    if resource.startswith("keys/"):
        row = next((key for key in state["keys"] if key["id"] == int(resource.split("/")[1])), None)
        if row is None:
            return error(404, "Key not found.")
        if method == "DELETE":
            row["revoked_at"] = now.isoformat()
        if method == "PATCH":
            body = await request.json()
            state["last_patch"] = body
            row.update(body)
            row["log_prompts"] = int(bool(row["log_prompts"]))
        return row
    if resource == "usage/recent":
        return {"data": [] if state["empty"] else state["requests"][:int(params.get("limit", 50))]}
    if resource == "usage":
        group = params.get("group_by", "key")
        rows = []
        for index in range(24 if group == "hour" else 3):
            count = [18, 25, 14, 8, 5, 11, 20, 27, 31, 36, 29, 47, 42, 56, 33, 39, 62, 45, 51, 37, 43, 66, 58, 49][index % 24]
            rows.append(dict(key=index + 1, key_name=state["keys"][index % 3]["name"],
                             model="gemma3:4b" if index % 2 == 0 else "qwen3.5:4b",
                             day=(now - timedelta(days=index)).strftime("%Y-%m-%d"),
                             hour=(now.replace(minute=0) - timedelta(hours=23 - index)).strftime("%Y-%m-%dT%H"),
                             requests=count if group == "hour" else 1200 + index * 240, errors=index % 5,
                             total_tokens=120000 + index * 500, prompt_tokens=40000, completion_tokens=80000,
                             avg_latency_ms=2400 + index * 200))
        return {"data": [] if state["empty"] else rows, "since": params.get("since"), "until": None, "group_by": group}
    if resource == "prompts":
        rows = state["prompts"]
        if params.get("key_id"):
            rows = [row for row in rows if row["key_id"] == int(params["key_id"])]
        if params.get("model"):
            rows = [row for row in rows if row["model"] == params["model"]]
        if params.get("status") == "error":
            rows = [row for row in rows if row["status_code"] >= 400]
        if params.get("status") == "ok":
            rows = [row for row in rows if row["status_code"] < 400]
        if params.get("q"):
            rows = [row for row in rows if params["q"].lower() in (row["prompt_preview"] + row["response_preview"]).lower()]
        if params.get("before"):
            rows = [row for row in rows if row["id"] < int(params["before"].rsplit("|", 1)[-1])]
        rows = [] if state["empty"] else rows[:50]
        previews = [{key: value for key, value in row.items() if key not in ("prompt", "response")} for row in rows]
        return {"data": previews, "next": f"{rows[-1]['ts']}|{rows[-1]['id']}" if len(rows) == 50 else None, "enabled": True, "retention_days": 30}
    if resource.startswith("prompts/"):
        row = next((row for row in state["prompts"] if row["id"] == int(resource.split("/")[1])), None)
        return row if row else error(404, "Prompt may have been pruned.")
    if resource == "models" and method == "GET":
        return {"data": [] if state["empty"] else state["models"]}
    if resource == "models/pull" and method == "GET":
        return {"data": state["jobs"]}
    if resource == "models/pull" and method == "POST":
        body = await request.json()
        job = {"id": str(len(state["jobs"]) + 1), "model": body["model"], "status": "running", "percent": 37}
        state["jobs"].append(job)
        return JSONResponse(job, status_code=202)
    if resource.startswith("models/"):
        name = resource.removeprefix("models/")
        row = next((row for row in state["models"] if row["name"] == name), None)
        if row is None:
            return error(404, "Model not found.")
        if method == "DELETE":
            state["models"].remove(row)
            return {"name": name, "deleted": True}
        row.update(await request.json())
        return row
    return error(404, "Not Found")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18001)
    args = parser.parse_args()
    print(f"Demo preview: http://127.0.0.1:{args.port}/admin/ui — admin key: {DEMO_KEY}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)

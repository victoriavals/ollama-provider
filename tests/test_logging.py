import asyncio
import json
import logging
import os
import time
from datetime import date, timedelta

import httpx
import pytest

from gateway import health
from gateway.apps.public import create_public_app
from gateway.logs import ACCESS_LOGGER, HEALTH_LOGGER, DailyFileHandler
from tests.conftest import fixture_json, fixture_text, hold_slots

CHAT = {"model": "gemma3:4b", "messages": [{"role": "user", "content": "hi"}]}


@pytest.fixture
def access_lines(caplog):
    caplog.set_level(logging.INFO, logger=ACCESS_LOGGER)

    def lines() -> list[dict]:
        return [json.loads(r.getMessage()) for r in caplog.records if r.name == ACCESS_LOGGER]
    return lines


async def test_access_log_records_key_model_and_tokens(public, auth, ollama, access_lines):
    ollama.post("/v1/chat/completions").respond(json=fixture_json("chat.json"))
    r = await public.post("/v1/chat/completions", headers={**auth, "CF-Connecting-IP": "203.0.113.7"}, json=CHAT)
    [line] = access_lines()
    assert line["request_id"] == r.headers["x-request-id"]
    assert line["app"] == "public" and line["method"] == "POST" and line["path"] == "/v1/chat/completions"
    assert (line["status"], line["ip"], line["model"], line["endpoint"]) == (200, "203.0.113.7", "gemma3:4b", "chat")
    assert line["key"].startswith("sk-") and line["key_name"] == "test"
    assert (line["prompt_tokens"], line["completion_tokens"], line["total_tokens"]) == (16, 7, 23)
    assert "error" not in line and line["duration_ms"] >= 0


async def test_access_log_records_rejections(public, auth, access_lines):
    await public.get("/v1/models", headers={"Authorization": "Bearer sk-bogus"})
    await public.post("/v1/chat/completions", headers=auth, json={**CHAT, "model": "nope"})
    unauth, missing = access_lines()
    assert (unauth["status"], unauth["error"]) == (401, "invalid_api_key") and "key" not in unauth
    assert (missing["status"], missing["error"], missing["model"]) == (404, "model_not_found", "nope")
    assert "sk-bogus" not in json.dumps(unauth)


async def test_access_log_shows_real_outcome_of_stream(public, auth, gw, ollama, access_lines):
    ollama.post("/v1/chat/completions").respond(200, content=fixture_text("chat_stream.txt").encode())
    await public.post("/v1/chat/completions", headers=auth, json={**CHAT, "stream": True})
    held = hold_slots(gw)
    await public.post("/v1/chat/completions", headers=auth, json={**CHAT, "stream": True})
    ok, busy = access_lines()
    assert (ok["status"], ok["stream"], ok["total_tokens"]) == (200, True, 22)
    assert (busy["status"], busy["error"]) == (503, "server_busy")  # HTTP header said 200
    assert ok["queue_ms"] >= 0 and busy["queue_ms"] >= 250  # waited QUEUE_TIMEOUT_S (0.3 s)
    for ticket in held:
        ticket.close()


async def test_unhandled_error_is_logged_as_internal_error(gw, auth, access_lines, monkeypatch):
    async def boom(name):
        raise RuntimeError("db exploded")
    monkeypatch.setattr(gw.db, "is_model_enabled", boom)
    # raise_app_exceptions=False: behave like uvicorn (client gets the 500, test does not re-raise)
    transport = httpx.ASGITransport(app=create_public_app(gw), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://public") as client:
        r = await client.post("/v1/chat/completions", headers=auth, json=CHAT)
    assert r.status_code == 500 and r.json()["error"]["code"] == "internal_error"
    assert "db exploded" not in r.text
    [line] = access_lines()
    assert (line["status"], line["error"], line["model"]) == (500, "internal_error", "gemma3:4b")


async def test_health_probes_are_not_logged(public, access_lines):
    await public.get("/health")
    await public.get("/health/ready")
    assert access_lines() == []


async def test_admin_changes_are_access_logged_but_reads_are_not(admin, access_lines):
    await admin.post("/admin/keys", json={"name": "audit-me"})
    await admin.get("/admin/keys")
    await admin.get("/admin/keys", headers={"Authorization": "Bearer wrong"})
    created, denied = access_lines()
    assert (created["app"], created["path"], created["status"]) == ("admin", "/admin/keys", 201)
    assert (denied["status"], denied["error"]) == (401, "invalid_admin_key")


def test_daily_file_handler_switches_day_and_purges(tmp_path):
    old = tmp_path / f"access-{date.today() - timedelta(days=40):%Y-%m-%d}.log"
    old.write_text("old\n")
    handler = DailyFileHandler(tmp_path, "access", retention_days=30)
    assert not old.exists()

    logger = logging.getLogger("test.daily")
    logger.propagate = False
    logger.addHandler(handler)
    try:
        logger.warning("today")
        tomorrow = logging.LogRecord("test.daily", logging.WARNING, __file__, 1, "tomorrow", None, None)
        tomorrow.created = time.time() + 86400
        handler.handle(tomorrow)
    finally:
        logger.removeHandler(handler)
        handler.close()
    today_file = tmp_path / f"access-{date.today():%Y-%m-%d}.log"
    next_file = tmp_path / f"access-{date.today() + timedelta(days=1):%Y-%m-%d}.log"
    assert today_file.read_text().strip() == "today"
    assert next_file.read_text().strip() == "tomorrow"


async def test_health_transitions_are_logged_once(gw, caplog, monkeypatch):
    caplog.set_level(logging.INFO, logger=HEALTH_LOGGER)
    gpu = [{"name": "RTX", "utilization_pct": 10.0, "memory_used_mib": 4000.0, "memory_total_mib": 8188.0,
            "temperature_c": 50.0, "power_w": 30.0}]
    monkeypatch.setattr(health, "gpu_stats", lambda: gpu)
    monkeypatch.setattr(health, "service_states", lambda names: {"OllamaServe": "running", "Cloudflared": "not_installed"})
    monitor = gw.health

    snap = await monitor.check()
    comps = snap["components"]
    assert comps["ollama"]["status"] == "ok" and comps["service:Cloudflared"]["status"] == "absent"
    assert comps["gpu"]["detail"] == "50C, 10% util, 3.9/8.0 GB"
    monitor._log_transitions(comps)
    monitor._log_transitions(comps)  # unchanged -> nothing new
    initial = [r for r in caplog.records if r.name == HEALTH_LOGGER]
    assert all(r.levelno == logging.INFO for r in initial)
    assert len(initial) == len(comps)

    caplog.clear()
    monkeypatch.setattr(health, "service_states", lambda names: {"OllamaServe": "stopped", "Cloudflared": "not_installed"})
    gpu[0]["temperature_c"] = 90.0
    monitor._log_transitions((await monitor.check())["components"])
    warnings = sorted(r.getMessage() for r in caplog.records if r.levelno == logging.WARNING)
    assert warnings[0].startswith("gpu is warn: 90C") and warnings[1].startswith("service:OllamaServe is down")

    caplog.clear()
    monkeypatch.setattr(health, "service_states", lambda names: {"OllamaServe": "running", "Cloudflared": "not_installed"})
    monitor._log_transitions((await monitor.check())["components"])
    assert any("service:OllamaServe recovered" in r.getMessage() for r in caplog.records)


async def test_rewarm_when_ollama_has_nothing_loaded(gw, monkeypatch):
    calls = []

    async def fake_warmup():
        calls.append(1)
    monkeypatch.setattr(gw, "warmup", fake_warmup)
    gw.settings.warmup = True
    monitor = gw.health
    monitor._last_rewarm = 0.0
    monitor._rewarm_if_unloaded({"reachable": True, "loaded_models": []})
    monitor._rewarm_if_unloaded({"reachable": True, "loaded_models": []})  # rate-limited
    monitor._last_rewarm = 0.0
    monitor._rewarm_if_unloaded({"reachable": True, "loaded_models": [{"name": "gemma3:4b"}]})
    monitor._rewarm_if_unloaded({"reachable": False, "error": "down"})
    held = hold_slots(gw, "qwen3.5:4b", 1)  # traffic running: "nothing loaded" is a model swap
    monitor._rewarm_if_unloaded({"reachable": True, "loaded_models": []})
    held[0].close()
    await asyncio.sleep(0)
    assert calls == [1]


def test_disk_usage_reports_free_space(tmp_path):
    [disk] = health.disk_usage([tmp_path, tmp_path / "sub"])
    assert disk["path"] == tmp_path.anchor and disk["free_gb"] > 0


@pytest.mark.skipif(os.name != "nt", reason="Windows service manager only")
def test_service_states_on_windows():
    states = health.service_states(["EventLog", "DefinitelyNotAService123"])
    assert states == {"EventLog": "running", "DefinitelyNotAService123": "not_installed"}

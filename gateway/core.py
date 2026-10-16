"""Shared runtime state for the public and admin apps (one process, one instance)."""

import asyncio
import logging
import secrets
import time
from typing import Any

import httpx

from .auth import ApiKey, KeyCache, hash_key
from .config import Settings
from .db import Database, UsageWriter, utcnow
from .health import HealthMonitor
from .limits import ConcurrencyGate, FailedAuthLimiter, SlidingWindow
from .ollama import OllamaClient

log = logging.getLogger("gateway")

PRUNE_INTERVAL_S = 24 * 3600


class Gateway:
    def __init__(self, settings: Settings, ollama_transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self.db = Database(settings.db_path)
        self.usage = UsageWriter(self.db)
        self.ollama = OllamaClient(settings.ollama_base_url, settings.upstream_connect_timeout,
                                   settings.upstream_read_timeout, transport=ollama_transport)
        self.rpm = SlidingWindow(60.0)
        self.failed_auth = FailedAuthLimiter(settings.failed_auth_per_ip_per_min)
        self.gate = ConcurrencyGate(settings.global_concurrency)
        self.keys = KeyCache()
        self.pulls = PullManager(self)
        self.health = HealthMonitor(self)
        self.started_at = time.time()
        self._tasks: set[asyncio.Task] = set()

    async def start(self) -> None:
        await self.db.connect()
        await self.db.seed_models(self.settings.seed_model_list)
        self.usage.start()
        self.spawn(self._prune_loop(), "prune")
        if self.settings.warmup:
            self.spawn(self.warmup(), "warmup")
        if self.settings.health_interval_s > 0:
            self.spawn(self.health.run(), "health")
        log.info("gateway started: db=%s ollama=%s", self.settings.db_path, self.settings.ollama_base_url)

    async def stop(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await self.usage.stop()
        await self.ollama.aclose()
        await self.db.close()
        log.info("gateway stopped")

    def spawn(self, coro, name: str) -> asyncio.Task:
        task = asyncio.create_task(coro, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def lookup_key(self, token: str) -> ApiKey | None:
        key_hash = hash_key(token)
        found, key = self.keys.get(key_hash)
        if found:
            return key
        row = await self.db.get_active_key_by_hash(key_hash)
        key = ApiKey(row["id"], row["name"], row["prefix"], row["rpm_limit"], row["max_concurrent"]) if row else None
        self.keys.put(key_hash, key)
        return key

    async def _prune_loop(self) -> None:
        while True:
            try:
                removed = await self.db.prune_usage(self.settings.usage_retention_days)
                if removed:
                    log.info("pruned %d usage rows older than %d days", removed, self.settings.usage_retention_days)
            except Exception:
                log.exception("usage prune failed")
            await asyncio.sleep(PRUNE_INTERVAL_S)

    async def warmup(self) -> None:
        models = await self.db.enabled_models()
        if not models:
            return
        try:
            await self.ollama.warmup(models[0]["name"])
        except Exception as exc:
            log.warning("warm-up of %s failed: %r", models[0]["name"], exc)


class PullManager:
    """Runs `ollama pull` as background jobs; progress is polled via the admin API."""

    MAX_JOBS = 50

    def __init__(self, gw: Gateway):
        self.gw = gw
        self.jobs: dict[str, dict[str, Any]] = {}

    def start(self, model: str, enable: bool) -> dict[str, Any]:
        for job in self.jobs.values():
            if job["model"] == model and job["status"] == "running":
                return job
        self._trim()
        job = {
            "id": "pull_" + secrets.token_hex(6), "model": model, "enable": enable, "status": "running",
            "detail": None, "completed": None, "total": None, "percent": None, "error": None,
            "started_at": utcnow(), "finished_at": None,
        }
        self.jobs[job["id"]] = job
        self.gw.spawn(self._run(job), f"pull:{model}")
        return job

    def running(self) -> int:
        return sum(1 for j in self.jobs.values() if j["status"] == "running")

    def _trim(self) -> None:
        finished = [jid for jid, j in self.jobs.items() if j["status"] != "running"]
        for jid in finished[: max(0, len(self.jobs) - self.MAX_JOBS + 1)]:
            del self.jobs[jid]

    async def _run(self, job: dict[str, Any]) -> None:
        try:
            async for event in self.gw.ollama.pull(job["model"]):
                job["detail"] = event.get("status")
                if event.get("total"):
                    job["total"] = event["total"]
                    job["completed"] = event.get("completed", 0)
                    job["percent"] = round(100 * job["completed"] / job["total"], 1)
            job["status"] = "success"
            if job["enable"]:
                await self.gw.db.set_model_enabled(job["model"], True)
            log.info("pulled model %s", job["model"])
        except asyncio.CancelledError:
            job["status"] = "cancelled"
            raise
        except Exception as exc:
            job["status"] = "error"
            job["error"] = str(exc)[:500]
            log.error("pull of %s failed: %s", job["model"], job["error"])
        finally:
            job["finished_at"] = utcnow()

"""Thin async client for Ollama's native API (model info, pull, delete, warm-up)."""

import json
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx

from .errors import APIError

log = logging.getLogger("gateway.ollama")

CAPS_TTL_S = 600.0


@dataclass(frozen=True)
class ModelInfo:
    capabilities: frozenset[str]
    architecture: str | None  # e.g. "gemma3", "qwen35" (decides parallel slots, see scheduler.py)


class OllamaClient:
    def __init__(self, base_url: str, connect_timeout: float, read_timeout: float,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.http = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(connect=connect_timeout, read=read_timeout, write=30.0, pool=30.0),
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
            transport=transport,
        )
        self._info: dict[str, tuple[float, ModelInfo]] = {}

    async def aclose(self) -> None:
        await self.http.aclose()

    async def _get(self, path: str, timeout: float | None = None) -> Any:
        try:
            resp = await self.http.get(path, timeout=timeout or httpx.USE_CLIENT_DEFAULT)
        except httpx.TransportError as exc:
            raise upstream_unavailable(exc) from exc
        resp.raise_for_status()
        return resp.json()

    async def version(self, timeout: float = 3.0) -> str:
        return (await self._get("/api/version", timeout))["version"]

    async def tags(self) -> list[dict[str, Any]]:
        return (await self._get("/api/tags", 10.0)).get("models", [])

    async def ps(self) -> list[dict[str, Any]]:
        return (await self._get("/api/ps", 10.0)).get("models", [])

    async def capabilities(self, model: str) -> frozenset[str]:
        return (await self.model_info(model)).capabilities

    async def model_info(self, model: str) -> ModelInfo:
        cached = self._info.get(model)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        try:
            resp = await self.http.post("/api/show", json={"model": model}, timeout=10.0)
        except httpx.TransportError as exc:
            raise upstream_unavailable(exc) from exc
        if resp.status_code == 404:
            raise APIError(404, f"The model `{model}` is enabled but not installed on this server.",
                           code="model_not_found", param="model")
        resp.raise_for_status()
        body = resp.json()
        info = ModelInfo(
            capabilities=frozenset(body.get("capabilities") or ["completion"]),
            architecture=(body.get("model_info") or {}).get("general.architecture")
            or (body.get("details") or {}).get("family"),
        )
        self._info[model] = (time.monotonic() + CAPS_TTL_S, info)
        return info

    def invalidate(self, model: str | None = None) -> None:
        if model is None:
            self._info.clear()
        else:
            self._info.pop(model, None)

    async def pull(self, model: str) -> AsyncIterator[dict[str, Any]]:
        async with self.http.stream("POST", "/api/pull", json={"model": model, "stream": True},
                                    timeout=httpx.Timeout(30.0, read=600.0)) as resp:
            if resp.status_code != 200:
                raise RuntimeError(f"pull failed: HTTP {resp.status_code} {(await resp.aread())[:300]!r}")
            async for line in resp.aiter_lines():
                if line.strip():
                    event = json.loads(line)
                    if "error" in event:
                        raise RuntimeError(event["error"])
                    yield event
        self.invalidate(model)

    async def delete(self, model: str) -> bool:
        resp = await self.http.request("DELETE", "/api/delete", json={"model": model}, timeout=30.0)
        self.invalidate(model)
        if resp.status_code == 404:
            return False
        resp.raise_for_status()
        return True

    async def warmup(self, model: str) -> None:
        """Load the model and run one token, so the first client request is not slow.

        A load alone leaves the first real inference ~3 s slower (CUDA setup). keep_alive is left
        to the server's OLLAMA_KEEP_ALIVE.
        """
        started = time.monotonic()
        resp = await self.http.post("/api/generate", timeout=300.0, json={
            "model": model, "prompt": "hi", "stream": False, "options": {"num_predict": 1}})
        resp.raise_for_status()
        log.info("warm-up %s ready in %.1fs", model, time.monotonic() - started)


def upstream_unavailable(exc: Exception) -> APIError:
    log.error("ollama unreachable: %r", exc)
    return APIError(503, "Model server is unavailable. Try again shortly.", type_="server_error",
                    code="upstream_unavailable", headers={"Retry-After": "10"})

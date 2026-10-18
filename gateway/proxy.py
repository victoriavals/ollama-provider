"""Forwarding to Ollama's OpenAI-compatible /v1 endpoints (JSON and SSE streaming)."""

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import httpx
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.requests import ClientDisconnect

from .auth import ApiKey
from .db import UsageRecord
from .errors import APIError, error_body
from .ollama import upstream_unavailable
from .scheduler import Ticket

if TYPE_CHECKING:
    from .core import Gateway

log = logging.getLogger("gateway.proxy")


@dataclass
class RequestContext:
    key: ApiKey
    endpoint: str
    model: str
    request_id: str
    client_ip: str
    started: float = field(default_factory=time.monotonic)
    rate_headers: dict[str, str] = field(default_factory=dict)
    access: dict[str, Any] = field(default_factory=dict)  # request.state.access (access-log fields)
    gpu_capacity: int | None = None  # parallel slots of this model (scheduler.resolve_capacity)


class Finalizer:
    """Releases the GPU ticket and key slot and records usage exactly once, however the request ends."""

    def __init__(self, gw: "Gateway", ctx: RequestContext, stream: bool):
        self.gw = gw
        self.ctx = ctx
        self.ticket: Ticket | None = None
        self.record = UsageRecord(
            key_id=ctx.key.id, endpoint=ctx.endpoint, model=ctx.model, status_code=200,
            request_id=ctx.request_id, stream=stream, client_ip=ctx.client_ip,
        )
        self._done = False

    def __call__(self) -> None:
        if self._done:
            return
        self._done = True
        if self.ticket is not None:
            self.ctx.access["queue_ms"] = self.ticket.queue_ms
            self.ticket.close()
        self.gw.gate.release_key(self.ctx.key.id)
        rec = self.record
        rec.latency_ms = int((time.monotonic() - self.ctx.started) * 1000)
        self.gw.usage.submit(rec)
        # The access log records the real outcome (a stream can end in 499/503 after a 200 header).
        self.ctx.access.update(
            status=rec.status_code, stream=rec.stream, prompt_tokens=rec.prompt_tokens,
            completion_tokens=rec.completion_tokens, total_tokens=rec.total_tokens,
        )
        if rec.status_code >= 500 or rec.status_code == 499:
            log.warning("%s %s model=%s ended with status %s after %d ms", rec.request_id, rec.endpoint,
                        rec.model, rec.status_code, rec.latency_ms)


class ClosingStreamingResponse(StreamingResponse):
    """StreamingResponse that always closes its generator (which closes the upstream stream)."""

    def __init__(self, content: AsyncIterator[str], on_close: Callable[[], None], **kwargs: Any):
        super().__init__(content, **kwargs)
        self._on_close = on_close

    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        except ClientDisconnect:
            pass
        finally:
            await self.body_iterator.aclose()
            self._on_close()


def server_busy(reason: str = "no GPU slot became free in time") -> APIError:
    return APIError(503, f"Server is busy: {reason}. Please retry shortly.",
                    type_="server_error", code="server_busy", headers={"Retry-After": "5"})


def _enqueue(gw: "Gateway", fin: Finalizer) -> None:
    """Take a place in the GPU queue (owned by the Finalizer); 503 at once when the queue is full."""
    fin.ticket = gw.scheduler.enqueue(fin.ctx.model, fin.ctx.gpu_capacity, gw.settings.queue_timeout_s)
    if fin.ticket is None:
        raise server_busy("the request queue is full")


def map_transport_error(exc: httpx.TransportError) -> APIError:
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
        return upstream_unavailable(exc)
    if isinstance(exc, httpx.TimeoutException):
        return APIError(504, "Model server timed out.", type_="server_error", code="upstream_timeout")
    log.error("upstream transport error: %r", exc)
    return APIError(502, "Model server connection failed.", type_="server_error", code="bad_gateway")


def sse_event(obj: dict[str, Any]) -> str:
    return f"data: {json.dumps(obj, separators=(',', ':'))}\n\n"


def _upstream_error(raw: bytes, status: int) -> dict[str, Any]:
    try:
        body = json.loads(raw)
        if isinstance(body, dict) and isinstance(body.get("error"), dict):
            return body
    except ValueError:
        pass
    return error_body(f"Model server error (HTTP {status}).", "server_error")


async def forward_json(gw: "Gateway", path: str, payload: dict[str, Any], ctx: RequestContext) -> Response:
    fin = Finalizer(gw, ctx, stream=False)
    try:
        _enqueue(gw, fin)
        if not await fin.ticket.wait():
            raise server_busy()
        try:
            resp = await gw.ollama.http.post(path, json=payload)
        except httpx.TransportError as exc:
            raise map_transport_error(exc) from exc
        try:
            data = resp.json()
        except ValueError:
            raise APIError(502, "Invalid response from model server.", type_="server_error",
                           code="bad_gateway") from None
        fin.record.status_code = resp.status_code
        if resp.status_code == 200 and isinstance(data, dict):
            fin.record.set_usage(data.get("usage"))
        elif resp.status_code >= 400:
            ctx.access.setdefault("error", "upstream_error")
        return JSONResponse(data, status_code=resp.status_code, headers=ctx.rate_headers)
    except APIError as exc:
        fin.record.status_code = exc.status_code
        exc.headers = {**ctx.rate_headers, **exc.headers}
        raise
    except asyncio.CancelledError:
        fin.record.status_code = 499
        raise
    finally:
        fin()


def stream_sse(gw: "Gateway", path: str, payload: dict[str, Any], ctx: RequestContext,
               include_usage: bool) -> Response:
    fin = Finalizer(gw, ctx, stream=True)
    try:
        _enqueue(gw, fin)  # before the response starts, so a full queue is a real HTTP 503
    except APIError as exc:
        fin.record.status_code = exc.status_code
        exc.headers = {**ctx.rate_headers, **exc.headers}
        fin()
        raise
    headers = {**ctx.rate_headers, "Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return ClosingStreamingResponse(_sse(gw, path, payload, include_usage, fin), on_close=fin,
                                    media_type="text/event-stream", headers=headers)


async def _sse(gw: "Gateway", path: str, payload: dict[str, Any], include_usage: bool,
               fin: Finalizer) -> AsyncIterator[str]:
    s = gw.settings
    ticket = fin.ticket
    try:
        # First byte goes out immediately; pings (SSE comments, with the position in the model's
        # queue) keep tunnels with a first-byte limit (Cloudflare: 100 s) happy while queued.
        yield ": connected\n\n"
        while (granted := await ticket.wait(s.stream_ping_interval_s)) is None:
            position = gw.scheduler.position(ticket)
            yield f": ping queue={position}\n\n" if position else ": ping\n\n"
        if not granted:
            raise server_busy()

        async with gw.ollama.http.stream("POST", path, json=payload) as resp:
            if resp.status_code != 200:
                fin.record.status_code = resp.status_code
                fin.ctx.access.setdefault("error", "upstream_error")
                yield sse_event(_upstream_error(await resp.aread(), resp.status_code))
                return
            async for line in resp.aiter_lines():
                if not line:
                    continue
                if line.startswith("data:"):
                    data = line[5:].strip()
                    if data != "[DONE]":
                        try:
                            chunk = json.loads(data)
                        except ValueError:
                            chunk = None
                        if isinstance(chunk, dict) and chunk.get("usage") and not chunk.get("choices"):
                            fin.record.set_usage(chunk["usage"])
                            if not include_usage:
                                continue
                yield line + "\n\n"
    except APIError as exc:
        fin.record.status_code = exc.status_code
        fin.ctx.access.setdefault("error", exc.code or exc.type)
        yield sse_event(exc.body())
    except httpx.TransportError as exc:
        err = map_transport_error(exc)
        fin.record.status_code = err.status_code
        fin.ctx.access.setdefault("error", err.code)
        yield sse_event(err.body())
    except (asyncio.CancelledError, GeneratorExit):
        fin.record.status_code = 499
        fin.ctx.access.setdefault("error", "client_disconnected")
        raise
    finally:
        fin()  # leaves the queue or frees the GPU slot, whichever applies

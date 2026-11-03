"""Public app (port 8000): the only thing Cloudflare Tunnel routes to."""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import JSONResponse, Response

from ..auth import ApiKey, client_ip, require_api_key
from ..core import Gateway
from ..db import PromptRecord, UsageRecord
from ..errors import APIError, install_handlers
from ..limits import RateResult
from ..middleware import RequestContextMiddleware
from ..promptlog import capture_prompt, estimate_prompt_tokens, new_record, placeholder_record
from ..proxy import RequestContext, forward_json, stream_sse
from ..scheduler import resolve_capacity
from ..sanitize import (
    model_not_found,
    read_json_body,
    require_model,
    sanitize_chat,
    sanitize_completion,
    sanitize_embeddings,
)

router = APIRouter()
log = logging.getLogger("gateway.public")

ENDPOINTS = {
    "chat": ("/v1/chat/completions", sanitize_chat),
    "completions": ("/v1/completions", sanitize_completion),
    "embeddings": ("/v1/embeddings", None),
}


def _epoch(iso: str) -> int:
    try:
        return int(datetime.fromisoformat(iso).timestamp())
    except ValueError:
        return 0


@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@router.get("/health/ready")
async def ready(request: Request) -> Response:
    try:
        await request.app.state.gw.ollama.version()
    except Exception:
        return JSONResponse({"status": "unavailable"}, status_code=503)
    return JSONResponse({"status": "ready"})


@router.get("/v1/models")
async def list_models(request: Request, key: ApiKey = Depends(require_api_key)) -> dict:
    rows = await request.app.state.gw.db.enabled_models()
    return {
        "object": "list",
        "data": [{"id": r["name"], "object": "model", "created": _epoch(r["added_at"]),
                  "owned_by": "ollama-provider"} for r in rows if key.may_use(r["name"])],
    }


@router.post("/v1/chat/completions")
async def chat_completions(request: Request, key: ApiKey = Depends(require_api_key)) -> Response:
    return await _generate(request, key, "chat")


@router.post("/v1/completions")
async def completions(request: Request, key: ApiKey = Depends(require_api_key)) -> Response:
    return await _generate(request, key, "completions")


@router.post("/v1/embeddings")
async def embeddings(request: Request, key: ApiKey = Depends(require_api_key)) -> Response:
    return await _generate(request, key, "embeddings")


async def _generate(request: Request, key: ApiKey, endpoint: str) -> Response:
    gw: Gateway = request.app.state.gw
    s = gw.settings
    path, sanitizer = ENDPOINTS[endpoint]
    request_id = getattr(request.state, "request_id", "req_unknown")
    access: dict = getattr(request.state, "access", {})
    ip = client_ip(request)
    rate: RateResult | None = None
    quota_headers: dict[str, str] = {}
    model: str | None = None
    prompt: PromptRecord | None = None
    access["endpoint"] = endpoint
    try:
        rate = gw.rpm.hit(key.id, key.rpm_limit or s.default_rpm)
        if not rate.allowed:
            raise APIError(429, f"Rate limit reached: {rate.limit} requests per minute for this API key.",
                           code="rate_limit_exceeded")
        if key.daily_token_limit is not None or key.monthly_token_limit is not None:
            quota = await gw.quota.status(key)
            quota_headers = quota.headers()
            quota.check()  # 429 insufficient_quota
        body = await read_json_body(request, s.max_body_bytes)
        model = require_model(body)
        access["model"] = model
        if s.prompt_log_enabled and key.log_prompts:
            try:  # raw body first, so requests rejected below are logged too
                prompt = new_record(body, endpoint, key, model, request_id, ip, s.prompt_log_max_chars)
            except Exception:  # the audit log must never break a request (or go missing)
                log.exception("%s prompt log capture failed", request_id)
                prompt = placeholder_record(body, endpoint, key, model, request_id, ip)
        # A model outside the key's allowlist looks exactly like an unknown one (as with OpenAI).
        if not key.may_use(model) or not await gw.db.is_model_enabled(model):
            raise model_not_found(model)
        info = await gw.ollama.model_info(model)
        caps = info.capabilities
        if sanitizer is None:
            payload, include_usage = sanitize_embeddings(body, caps, s), False
        else:
            payload, include_usage = sanitizer(body, caps, s)
        if prompt is not None:  # store exactly what the model receives (cleaned keys, capped fields)
            try:
                prompt.prompt, prompt.prompt_preview, prompt.prompt_chars = capture_prompt(
                    payload, endpoint, s.prompt_log_max_chars)
            except Exception:
                log.exception("%s prompt log capture failed", request_id)
        gpu_capacity = resolve_capacity(s, model, info.architecture)
        # Last step that can fail: everything after it must leave the key slot to the Finalizer.
        if not gw.gate.try_acquire_key(key.id, key.max_concurrent or s.default_max_concurrent):
            raise APIError(429, "Too many concurrent requests for this API key.",
                           code="concurrency_limit_exceeded", headers={"Retry-After": "2"})
    except APIError as exc:
        exc.headers = {**(rate.headers() if rate else {}), **quota_headers, **exc.headers}
        gw.usage.submit(UsageRecord(key_id=key.id, endpoint=endpoint, model=model, status_code=exc.status_code,
                                    request_id=request_id, client_ip=ip, latency_ms=0))
        if prompt is not None:  # rejected after the body was read (model not allowed, bad field, ...)
            prompt.status_code = exc.status_code
            gw.usage.submit(prompt)
        raise

    # From here the key slot is owned by the proxy, which releases it when the request ends.
    ctx = RequestContext(key=key, endpoint=endpoint, model=model, request_id=request_id, client_ip=ip,
                         rate_headers={**rate.headers(), **quota_headers}, access=access, gpu_capacity=gpu_capacity,
                         prompt=prompt, prompt_tokens_est=estimate_prompt_tokens(payload))
    if payload.get("stream"):
        return stream_sse(gw, path, payload, ctx, include_usage)
    return await forward_json(gw, path, payload, ctx)


def create_public_app(gw: Gateway) -> FastAPI:
    app = FastAPI(title="ollama-provider", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.gw = gw
    install_handlers(app)
    app.add_middleware(RequestContextMiddleware, app_name="public", ip_headers=gw.settings.client_ip_header_list)
    app.include_router(router)
    return app

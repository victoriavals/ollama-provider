"""Admin app (port 8001): bound to loopback and never routed through the tunnel."""

import logging
from datetime import datetime, timezone
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, FastAPI, Query, Request
from pydantic import BaseModel, Field

from ..auth import display_prefix, generate_key, hash_key, require_admin
from ..config import BASE_DIR
from ..core import Gateway
from ..db import days_ago
from ..errors import APIError, install_handlers
from ..middleware import RequestContextMiddleware

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])
audit = logging.getLogger("gateway.admin")


class KeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    rpm_limit: int | None = Field(default=None, ge=1, le=100_000)
    max_concurrent: int | None = Field(default=None, ge=1, le=64)


class KeyUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    rpm_limit: int | None = Field(default=None, ge=1, le=100_000)
    max_concurrent: int | None = Field(default=None, ge=1, le=64)


class ModelPull(BaseModel):
    model: str = Field(min_length=1, max_length=200)
    enable: bool = False


class ModelUpdate(BaseModel):
    enabled: bool


def _gw(request: Request) -> Gateway:
    return request.app.state.gw


def _key_out(gw: Gateway, row: dict) -> dict:
    s = gw.settings
    return {**row,
            "rpm_limit_effective": row["rpm_limit"] or s.default_rpm,
            "max_concurrent_effective": row["max_concurrent"] or s.default_max_concurrent}


# ---- Keys ------------------------------------------------------------------

@router.post("/keys", status_code=201)
async def create_key(body: KeyCreate, request: Request) -> dict:
    gw = _gw(request)
    key = generate_key()
    row = await gw.db.create_key(body.name, hash_key(key), display_prefix(key), body.rpm_limit, body.max_concurrent)
    audit.info("key created id=%s name=%r prefix=%s rpm=%s max_concurrent=%s",
               row["id"], row["name"], row["prefix"], row["rpm_limit"], row["max_concurrent"])
    return {**_key_out(gw, row), "key": key, "note": "Store this key now; it cannot be shown again."}


@router.get("/keys")
async def list_keys(request: Request, include_revoked: bool = True) -> dict:
    gw = _gw(request)
    return {"data": [_key_out(gw, r) for r in await gw.db.list_keys(include_revoked)]}


@router.patch("/keys/{key_id}")
async def update_key(key_id: int, body: KeyUpdate, request: Request) -> dict:
    gw = _gw(request)
    fields = {k: getattr(body, k) for k in body.model_fields_set}
    if fields.get("name", "") is None:
        raise APIError(400, "name cannot be null.", param="name")
    row = await gw.db.update_key(key_id, fields)
    if row is None:
        raise APIError(404, f"Key {key_id} not found.")
    gw.keys.clear()
    audit.info("key updated id=%s fields=%s", key_id, fields)
    return _key_out(gw, row)


@router.delete("/keys/{key_id}")
async def revoke_key(key_id: int, request: Request) -> dict:
    gw = _gw(request)
    if not await gw.db.revoke_key(key_id):
        raise APIError(404, f"Key {key_id} not found or already revoked.")
    gw.keys.clear()
    audit.info("key revoked id=%s", key_id)
    return {"id": key_id, "revoked": True}


# ---- Usage -----------------------------------------------------------------

@router.get("/usage")
async def usage(
    request: Request,
    since: str | None = Query(None, description="ISO date/time (UTC). Default: 7 days ago."),
    until: str | None = None,
    key_id: int | None = None,
    group_by: Literal["day", "key", "model"] = "day",
) -> dict:
    since_iso = _iso(since, "since") if since else days_ago(7)
    until_iso = _iso(until, "until") if until else None
    rows = await _gw(request).db.usage_summary(since_iso, until_iso, key_id, group_by)
    return {"since": since_iso, "until": until_iso, "group_by": group_by, "data": rows}


@router.get("/usage/recent")
async def usage_recent(request: Request, limit: int = Query(50, ge=1, le=1000), key_id: int | None = None) -> dict:
    return {"data": await _gw(request).db.recent_usage(limit, key_id)}


def _iso(value: str, param: str) -> str:
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        raise APIError(400, f"{param} must be an ISO date or datetime.", param=param) from None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


# ---- Models ----------------------------------------------------------------

@router.get("/models")
async def list_models(request: Request) -> dict:
    gw = _gw(request)
    allow = {r["name"]: bool(r["enabled"]) for r in await gw.db.list_models()}
    installed = {m["name"]: m for m in await gw.ollama.tags()}
    data = []
    for name in sorted(set(allow) | set(installed)):
        m = installed.get(name, {})
        details = m.get("details", {})
        data.append({
            "name": name, "installed": name in installed, "enabled": allow.get(name, False),
            "size_bytes": m.get("size"), "parameter_size": details.get("parameter_size"),
            "quantization": details.get("quantization_level"), "modified_at": m.get("modified_at"),
        })
    return {"data": data}


@router.patch("/models/{name:path}")
async def update_model(name: str, body: ModelUpdate, request: Request) -> dict:
    gw = _gw(request)
    if body.enabled and name not in {m["name"] for m in await gw.ollama.tags()}:
        raise APIError(404, f"Model `{name}` is not installed. Pull it first.", param="name")
    await gw.db.set_model_enabled(name, body.enabled)
    audit.info("model %s %s", name, "enabled" if body.enabled else "disabled")
    return {"name": name, "enabled": body.enabled}


@router.post("/models/pull", status_code=202)
async def pull_model(body: ModelPull, request: Request) -> dict:
    job = _gw(request).pulls.start(body.model, body.enable)
    audit.info("model pull requested model=%s enable=%s job=%s", body.model, body.enable, job["id"])
    return job


@router.get("/models/pull")
async def list_pulls(request: Request) -> dict:
    return {"data": list(_gw(request).pulls.jobs.values())}


@router.get("/models/pull/{job_id}")
async def get_pull(job_id: str, request: Request) -> dict:
    job = _gw(request).pulls.jobs.get(job_id)
    if job is None:
        raise APIError(404, f"Pull job {job_id} not found.")
    return job


@router.delete("/models/{name:path}")
async def delete_model(name: str, request: Request) -> dict:
    gw = _gw(request)
    try:
        existed = await gw.ollama.delete(name)
    except httpx.HTTPError as exc:
        raise APIError(502, f"Ollama delete failed: {exc}", type_="server_error") from exc
    await gw.db.delete_model(name)
    audit.info("model deleted %s (existed=%s)", name, existed)
    return {"name": name, "deleted": existed}


# ---- Status & maintenance --------------------------------------------------

@router.get("/status")
async def status(request: Request) -> dict:
    """Live check of every dependency (same data the health monitor logs)."""
    return await _gw(request).health.check()


@router.post("/backup")
async def backup(request: Request) -> dict:
    dest = BASE_DIR / "data" / "backups" / f"gateway-{datetime.now():%Y%m%d-%H%M%S}.db"
    await _gw(request).db.backup(dest)
    audit.info("database backup written to %s", dest)
    return {"path": str(dest)}


def create_admin_app(gw: Gateway) -> FastAPI:
    app = FastAPI(title="ollama-provider admin", docs_url="/admin/docs", redoc_url=None,
                  openapi_url="/admin/openapi.json")
    app.state.gw = gw
    install_handlers(app)
    app.add_middleware(RequestContextMiddleware, app_name="admin", ip_headers=gw.settings.client_ip_header_list)
    app.include_router(router)
    return app

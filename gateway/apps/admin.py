"""Admin app (port 8001): bound to loopback and never routed through the tunnel."""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, FastAPI, Query, Request
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from ..auth import display_prefix, generate_key, hash_key, require_admin
from ..config import BASE_DIR
from ..core import Gateway
from ..db import days_ago, utcnow
from ..errors import APIError, install_handlers
from ..middleware import RequestContextMiddleware
from ..quota import iso_utc, local_now, local_periods, parse_expiry

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])
audit = logging.getLogger("gateway.admin")


class KeyPolicy(BaseModel):
    """Optional per-key policy; null = no limit / every model / never expires / normal priority."""
    rpm_limit: int | None = Field(default=None, ge=1, le=100_000)
    max_concurrent: int | None = Field(default=None, ge=1, le=64)
    daily_token_limit: int | None = Field(default=None, ge=1, le=10**12)
    monthly_token_limit: int | None = Field(default=None, ge=1, le=10**12)
    expires_at: str | None = Field(default=None, max_length=40)  # ISO date (end of that local day) or datetime
    allowed_models: list[str] | None = Field(default=None, max_length=50)
    priority: int | None = Field(default=None, ge=0, le=9)
    log_prompts: bool | None = None  # null/omitted = default (logged)


class KeyCreate(KeyPolicy):
    name: str = Field(min_length=1, max_length=100)


class KeyUpdate(KeyPolicy):
    name: str | None = Field(default=None, min_length=1, max_length=100)


class ModelPull(BaseModel):
    model: str = Field(min_length=1, max_length=200)
    enable: bool = False


class ModelUpdate(BaseModel):
    enabled: bool


def _gw(request: Request) -> Gateway:
    return request.app.state.gw


def _key_out(gw: Gateway, row: dict, usage: dict[int, dict[str, int]]) -> dict:
    s = gw.settings
    used = usage.get(row["id"], {})
    return {**row,
            "rpm_limit_effective": row["rpm_limit"] or s.default_rpm,
            "max_concurrent_effective": row["max_concurrent"] or s.default_max_concurrent,
            "expired": row["expires_at"] is not None and row["expires_at"] <= utcnow(),
            "usage": {"day_tokens": used.get("day", 0), "month_tokens": used.get("month", 0)}}


async def _token_usage(gw: Gateway) -> tuple[dict[int, dict[str, int]], dict[str, str]]:
    """Tokens per key in the current local day/month, plus when those periods reset."""
    day, next_day, month, next_month = local_periods(local_now())
    usage = await gw.db.token_usage_by_key(iso_utc(day), iso_utc(month))
    return usage, {"day": next_day.isoformat(timespec="minutes"), "month": next_month.isoformat(timespec="minutes")}


async def _policy(gw: Gateway, fields: dict) -> dict:
    """Validate and normalise policy fields before they reach the database."""
    if fields.get("expires_at") is not None:
        try:
            fields["expires_at"] = parse_expiry(fields["expires_at"])
        except ValueError:
            raise APIError(400, "expires_at must be an ISO date (YYYY-MM-DD) or datetime between 2000 and 2100.",
                           param="expires_at") from None
    models = fields.get("allowed_models")
    if models is not None:
        if not models:
            raise APIError(400, "allowed_models cannot be empty; use null to allow every model.",
                           param="allowed_models")
        known = {m["name"] for m in await gw.db.list_models()}
        unknown = sorted(set(models) - known)
        if unknown:
            raise APIError(400, f"Unknown model(s) in allowed_models: {', '.join(unknown)}.", param="allowed_models")
        fields["allowed_models"] = sorted(set(models))
    return fields


# ---- Keys ------------------------------------------------------------------

@router.post("/keys", status_code=201)
async def create_key(body: KeyCreate, request: Request) -> dict:
    gw = _gw(request)
    key = generate_key()
    policy = await _policy(gw, body.model_dump(exclude={"name", "rpm_limit", "max_concurrent"}))
    row = await gw.db.create_key(body.name, hash_key(key), display_prefix(key), body.rpm_limit, body.max_concurrent,
                                 **policy)
    audit.info("key created id=%s name=%r prefix=%s rpm=%s max_concurrent=%s policy=%s", row["id"], row["name"],
               row["prefix"], row["rpm_limit"], row["max_concurrent"], {k: v for k, v in policy.items() if v is not None})
    usage, _ = await _token_usage(gw)
    return {**_key_out(gw, row, usage), "key": key, "note": "Store this key now; it cannot be shown again."}


@router.get("/keys")
async def list_keys(request: Request, include_revoked: bool = True) -> dict:
    gw = _gw(request)
    usage, resets = await _token_usage(gw)
    return {"data": [_key_out(gw, r, usage) for r in await gw.db.list_keys(include_revoked)], "quota_resets": resets}


@router.patch("/keys/{key_id}")
async def update_key(key_id: int, body: KeyUpdate, request: Request) -> dict:
    gw = _gw(request)
    fields = {k: getattr(body, k) for k in body.model_fields_set}
    if fields.get("name", "") is None:
        raise APIError(400, "name cannot be null.", param="name")
    row = await gw.db.update_key(key_id, await _policy(gw, fields))
    if row is None:
        raise APIError(404, f"Key {key_id} not found.")
    gw.keys.clear()
    audit.info("key updated id=%s fields=%s", key_id, fields)
    usage, _ = await _token_usage(gw)
    return _key_out(gw, row, usage)


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
    group_by: Literal["hour", "day", "key", "model"] = "day",
) -> dict:
    since_iso = _iso(since, "since") if since else days_ago(7)
    until_iso = _iso(until, "until") if until else None
    rows = await _gw(request).db.usage_summary(since_iso, until_iso, key_id, group_by)
    return {"since": since_iso, "until": until_iso, "group_by": group_by, "data": rows}


@router.get("/usage/recent")
async def usage_recent(request: Request, limit: int = Query(50, ge=1, le=1000), key_id: int | None = None) -> dict:
    return {"data": await _gw(request).db.recent_usage(limit, key_id)}


# ---- Prompt log --------------------------------------------------------------

@router.get("/prompts")
async def list_prompts(
    request: Request,
    since: str | None = None,
    until: str | None = None,
    key_id: int | None = None,
    model: str | None = Query(None, max_length=200),
    status: Literal["all", "ok", "error"] = "all",
    q: str | None = Query(None, max_length=200),
    before: str | None = Query(None, max_length=60),
    limit: int = Query(50, ge=1, le=100),
) -> dict:
    """Newest prompts first, previews only. Always bounded by a time range (default: last 24 h)."""
    gw = _gw(request)
    query = (q or "").strip() or None
    if query is not None and len(query) < 3:
        raise APIError(400, "q must be at least 3 characters.", param="q")
    cursor = None
    if before:
        ts, _, row_id = before.rpartition("|")
        if not ts or not row_id.isdigit():
            raise APIError(400, "before must be the 'next' value of a previous page.", param="before")
        cursor = (ts, int(row_id))
    rows = await gw.db.list_prompts(
        _iso(since, "since") if since else days_ago(1), _iso(until, "until") if until else None, key_id, model,
        None if status == "all" else status == "error", query, cursor, limit)
    return {"data": rows, "next": f"{rows[-1]['ts']}|{rows[-1]['id']}" if len(rows) == limit else None,
            "enabled": gw.settings.prompt_log_enabled, "retention_days": gw.settings.prompt_log_retention_days}


@router.get("/prompts/{prompt_id}")
async def get_prompt(prompt_id: int, request: Request) -> dict:
    row = await _gw(request).db.get_prompt(prompt_id)
    if row is None:
        raise APIError(404, f"Prompt {prompt_id} not found (it may have been pruned).")
    return row


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


@router.get("/info")
async def info(request: Request) -> dict:
    """Static facts the dashboard needs (public URL, default limits, enabled models)."""
    gw = _gw(request)
    s = gw.settings
    return {
        "public_base_url": s.public_base_url or None,
        "client_guide_url": s.client_guide_url or None,
        "defaults": {"rpm": s.default_rpm, "max_concurrent": s.default_max_concurrent,
                     "global_concurrency": s.global_concurrency, "max_tokens_cap": s.max_tokens_cap},
        "models": [m["name"] for m in await gw.db.enabled_models()],
    }


@router.post("/backup")
async def backup(request: Request) -> dict:
    dest = BASE_DIR / "data" / "backups" / f"gateway-{datetime.now():%Y%m%d-%H%M%S}.db"
    await _gw(request).db.backup(dest)
    audit.info("database backup written to %s", dest)
    return {"path": str(dest)}


# ---- Dashboard (static files, no secrets: the page asks for ADMIN_KEY itself) --

UI_DIR = Path(__file__).resolve().parent.parent / "static" / "dashboard"
UI_FILES = {"app.js": "text/javascript", "app.css": "text/css"}
# The page holds the admin key in memory: no third-party code, no framing, no inline script.
UI_HEADERS = {
    "Content-Security-Policy": ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                                "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"),
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}
ui = APIRouter(include_in_schema=False)


@ui.get("/")
async def ui_root() -> RedirectResponse:
    return RedirectResponse("/admin/ui")


@ui.get("/admin/ui")
async def ui_index() -> FileResponse:
    return FileResponse(UI_DIR / "index.html", media_type="text/html", headers=UI_HEADERS)


@ui.get("/admin/ui/{name}")
async def ui_asset(name: str) -> FileResponse:
    if name not in UI_FILES:
        raise APIError(404, "Not Found")
    return FileResponse(UI_DIR / name, media_type=UI_FILES[name], headers=UI_HEADERS)


def create_admin_app(gw: Gateway) -> FastAPI:
    app = FastAPI(title="ollama-provider admin", docs_url="/admin/docs", redoc_url=None,
                  openapi_url="/admin/openapi.json")
    app.state.gw = gw
    install_handlers(app)
    app.add_middleware(RequestContextMiddleware, app_name="admin", ip_headers=gw.settings.client_ip_header_list)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=gw.settings.admin_allowed_host_list)
    app.include_router(router)
    app.include_router(ui)
    return app

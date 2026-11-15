"""Serve a manifest-bounded Next.js export, with CSP hashes for its bootstrap scripts."""

import json
import re
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, RedirectResponse

from .errors import APIError

UI_DIR = Path(__file__).resolve().parent / "static" / "dashboard"
BUILD_ROOT = UI_DIR / "build"
LEGACY_ASSETS = {"app.js": "text/javascript", "app.css": "text/css"}
UI_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "font-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    ),
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}
ui = APIRouter(include_in_schema=False)


def _inside(root: Path, name: str) -> Path:
    candidate = (root / name).resolve()
    if not candidate.is_relative_to(root.resolve()) or not candidate.is_file():
        raise APIError(404, "Not Found")
    return candidate


@lru_cache(maxsize=4)
def _read_release(pointer_path: str, stamp: int) -> tuple[Path, dict]:
    # The timestamp invalidates this cache after an atomic pointer update.
    pointer = Path(pointer_path)
    try:
        release = json.loads(pointer.read_text(encoding="utf-8"))["release"]
        if not re.fullmatch(r"release-[A-Za-z0-9-]+", release):
            raise ValueError("Invalid release")
        root = pointer.parent / release
        manifest = json.loads(_inside(root, "manifest.json").read_text(encoding="utf-8"))
        if manifest.get("version") != 1 or not isinstance(manifest.get("pages"), dict) or not isinstance(manifest.get("assets"), dict):
            raise ValueError("Invalid manifest")
        return root, manifest
    except (KeyError, TypeError, ValueError, OSError) as exc:
        raise APIError(503, "Dashboard build is unavailable. Rebuild the frontend.", type_="server_error") from exc


def _current() -> tuple[Path, dict] | None:
    pointer = BUILD_ROOT / "active.json"
    try:
        stamp = pointer.stat().st_mtime_ns
    except FileNotFoundError:
        return None
    return _read_release(str(pointer), stamp)


def _page(root: Path, page: dict) -> FileResponse:
    hashes = page.get("script_hashes", [])
    if not isinstance(hashes, list) or any(not isinstance(value, str) or not re.fullmatch(r"sha256-[A-Za-z0-9+/]{43}=", value) for value in hashes):
        raise APIError(503, "Invalid dashboard CSP manifest.", type_="server_error")
    headers = dict(UI_HEADERS)
    if hashes:
        scripts = "script-src 'self' " + " ".join(f"'{value}'" for value in hashes)
        headers["Content-Security-Policy"] = headers["Content-Security-Policy"].replace("script-src 'self'", scripts)
    return FileResponse(_inside(root, page["file"]), media_type="text/html", headers=headers)


@ui.get("/")
async def ui_root() -> RedirectResponse:
    return RedirectResponse("/admin/ui")


@ui.get("/admin/ui")
@ui.get("/admin/ui/")
async def ui_index() -> FileResponse:
    current = _current()
    if current is None:
        # A source checkout remains usable before the first frontend build.
        return FileResponse(UI_DIR / "index.html", media_type="text/html", headers=UI_HEADERS)
    root, manifest = current
    return _page(root, manifest["pages"][""])


@ui.get("/admin/ui/{name:path}")
async def ui_asset(name: str) -> FileResponse:
    if not name or any(part in ("", ".", "..") or part.startswith(".") for part in name.rstrip("/").split("/")):
        raise APIError(404, "Not Found")
    current = _current()
    if current is None:
        if name not in LEGACY_ASSETS:
            raise APIError(404, "Not Found")
        return FileResponse(UI_DIR / name, media_type=LEGACY_ASSETS[name], headers=UI_HEADERS)
    root, manifest = current
    route = name.rstrip("/")
    if route in manifest["pages"]:
        return _page(root, manifest["pages"][route])
    media_type = manifest["assets"].get(name)
    if not media_type:
        raise APIError(404, "Not Found")
    return FileResponse(_inside(root, name), media_type=media_type, headers=UI_HEADERS)

import base64
import hashlib
import json
from pathlib import Path

import pytest

from gateway import dashboard


@pytest.fixture
def dashboard_build(tmp_path, monkeypatch):
    root = tmp_path / "dashboard"
    release = root / "release-test"
    release.mkdir(parents=True)
    code = "window.fixture = true;"
    digest = "sha256-" + base64.b64encode(hashlib.sha256(code.encode()).digest()).decode()
    (release / "index.html").write_text(f'<title>Kontrol Ollama Provider</title><script>{code}</script>', encoding="utf-8")
    (release / "_next").mkdir()
    (release / "_next" / "fixture.js").write_text("/* local bundle */", encoding="utf-8")
    (release / "private.txt").write_text("not in manifest", encoding="utf-8")
    manifest = {"version": 1, "pages": {"": {"file": "index.html", "script_hashes": [digest]},
                                          "keys": {"file": "index.html", "script_hashes": [digest]}},
                "assets": {"_next/fixture.js": "text/javascript"}}
    (release / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (root / "active.json").write_text(json.dumps({"release": "release-test"}), encoding="utf-8")
    monkeypatch.setattr(dashboard, "BUILD_ROOT", root)
    dashboard._read_release.cache_clear()
    return root, release, manifest, digest


async def test_static_pages_and_assets_use_manifest_and_script_hashes(admin, dashboard_build):
    _, _, _, digest = dashboard_build
    for path in ("/admin/ui", "/admin/ui/", "/admin/ui/keys", "/admin/ui/keys/"):
        response = await admin.get(path, headers={"Authorization": ""})
        assert response.status_code == 200
        csp = response.headers["content-security-policy"]
        assert f"'{digest}'" in csp and "unsafe-inline" not in csp and "unsafe-eval" not in csp
        assert "style-src 'self'" in csp and "font-src 'self'" in csp
        assert response.headers["x-frame-options"] == "DENY"
        assert response.headers["cache-control"] == "no-store"
    asset = await admin.get("/admin/ui/_next/fixture.js")
    assert asset.status_code == 200 and asset.headers["content-type"].startswith("text/javascript")


async def test_static_files_reject_unlisted_and_traversal_paths(admin, dashboard_build):
    for path in ("manifest.json", "private.txt", "index.html", "active.json", "does-not-exist/", "%2e%2e/private.txt", "_next/%2e%2e/private.txt", ".env"):
        assert (await admin.get(f"/admin/ui/{path}")).status_code == 404


async def test_bad_release_pointer_fails_closed(admin, dashboard_build):
    root, _, _, _ = dashboard_build
    (root / "active.json").write_text(json.dumps({"release": "../../outside"}), encoding="utf-8")
    dashboard._read_release.cache_clear()
    assert (await admin.get("/admin/ui")).status_code == 503


async def test_invalid_csp_hash_fails_closed(admin, dashboard_build):
    _, release, manifest, _ = dashboard_build
    manifest["pages"][""]["script_hashes"] = ["unsafe-inline"]
    (release / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    dashboard._read_release.cache_clear()
    assert (await admin.get("/admin/ui")).status_code == 503


async def test_asset_cannot_escape_release_root(admin, dashboard_build):
    root, release, manifest, _ = dashboard_build
    (root / "outside.js").write_text("not served", encoding="utf-8")
    manifest["pages"]["keys"]["file"] = "../outside.js"
    (release / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    dashboard._read_release.cache_clear()
    assert (await admin.get("/admin/ui/keys/")).status_code == 404


def test_application_sources_do_not_render_untrusted_html():
    frontend = Path(__file__).resolve().parents[1] / "frontend"
    for directory in (frontend / "views", frontend / "components"):
        for source in directory.glob("*.tsx"):
            code = source.read_text(encoding="utf-8")
            assert "dangerouslySetInnerHTML" not in code and "insertAdjacentHTML" not in code

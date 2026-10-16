from tests.conftest import new_key


async def test_missing_key_is_401_in_openai_format(public):
    r = await public.get("/v1/models")
    assert r.status_code == 401
    err = r.json()["error"]
    assert err["type"] == "authentication_error" and err["code"] == "invalid_api_key"
    assert r.headers["x-request-id"].startswith("req_")


async def test_invalid_key_is_401(public):
    r = await public.get("/v1/models", headers={"Authorization": "Bearer sk-not-a-real-key"})
    assert r.status_code == 401
    assert "sk-not" not in r.text  # never echo keys back


async def test_valid_key_lists_enabled_models(public, auth, gw):
    await gw.db.set_model_enabled("nomic-embed-text", False)
    r = await public.get("/v1/models", headers=auth)
    assert r.status_code == 200
    assert [m["id"] for m in r.json()["data"]] == ["gemma3:4b"]


async def test_failed_auth_is_rate_limited_per_ip(public, auth):
    headers = {"Authorization": "Bearer sk-wrong", "CF-Connecting-IP": "203.0.113.9"}
    for _ in range(5):
        assert (await public.get("/v1/models", headers=headers)).status_code == 401
    blocked = await public.get("/v1/models", headers=headers)
    assert blocked.status_code == 429 and blocked.headers["Retry-After"] == "60"
    # other IPs (and valid keys from them) are unaffected
    ok = await public.get("/v1/models", headers={**auth, "CF-Connecting-IP": "198.51.100.1"})
    assert ok.status_code == 200


async def test_failed_auth_uses_last_x_forwarded_for_entry(public):
    # Tailscale Funnel / ngrok append the real IP; a client-supplied first entry must not
    # let an attacker dodge the limit or get a victim's IP blocked.
    for i in range(5):
        headers = {"Authorization": "Bearer sk-wrong", "X-Forwarded-For": f"10.0.0.{i}, 203.0.113.50"}
        assert (await public.get("/v1/models", headers=headers)).status_code == 401
    blocked = await public.get("/v1/models", headers={"Authorization": "Bearer sk-wrong",
                                                      "X-Forwarded-For": "1.1.1.1, 203.0.113.50"})
    assert blocked.status_code == 429
    other = await public.get("/v1/models", headers={"Authorization": "Bearer sk-wrong",
                                                    "X-Forwarded-For": "203.0.113.50, 198.51.100.7"})
    assert other.status_code == 401


def test_scope_client_ip_header_priority():
    from gateway.middleware import scope_client_ip
    scope = {"client": ("127.0.0.1", 5000), "headers": [(b"x-forwarded-for", b"9.9.9.9, 198.51.100.7"),
                                                        (b"cf-connecting-ip", b"203.0.113.1")]}
    assert scope_client_ip(scope, ("cf-connecting-ip", "x-forwarded-for")) == "203.0.113.1"
    assert scope_client_ip(scope, ("x-forwarded-for",)) == "198.51.100.7"
    assert scope_client_ip({"client": ("127.0.0.1", 5000), "headers": []}, ("x-forwarded-for",)) == "127.0.0.1"


async def test_revoked_key_rejected_after_cache_clear(public, gw):
    key_id, key = await new_key(gw)
    headers = {"Authorization": f"Bearer {key}"}
    assert (await public.get("/v1/models", headers=headers)).status_code == 200
    await gw.db.revoke_key(key_id)
    gw.keys.clear()
    assert (await public.get("/v1/models", headers=headers)).status_code == 401


async def test_unknown_routes_and_docs_are_404(public):
    for path in ("/", "/docs", "/openapi.json", "/admin/keys", "/api/tags"):
        r = await public.get(path)
        assert r.status_code == 404, path
        assert "error" in r.json()


async def test_health_endpoints_need_no_key(public):
    assert (await public.get("/health")).json() == {"status": "ok"}
    assert (await public.get("/health/ready")).json() == {"status": "ready"}

import json
from pathlib import Path

import httpx
import pytest
import respx

from gateway.apps.admin import create_admin_app
from gateway.apps.public import create_public_app
from gateway.auth import display_prefix, generate_key, hash_key
from gateway.config import Settings
from gateway.core import Gateway

FIXTURES = Path(__file__).parent / "fixtures"
OLLAMA = "http://ollama.test"
ADMIN_KEY = "admin-test-key"

CAPS = {
    "gemma3:4b": ["completion", "vision"],
    "nomic-embed-text": ["embedding"],
}


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def fixture_json(name: str) -> dict:
    return json.loads(fixture_text(name))


def _show(request: httpx.Request) -> httpx.Response:
    model = json.loads(request.content)["model"]
    if model not in CAPS:
        return httpx.Response(404, json={"error": f"model '{model}' not found"})
    return httpx.Response(200, json={"capabilities": CAPS[model]})


@pytest.fixture
def ollama() -> respx.MockRouter:
    router = respx.MockRouter(base_url=OLLAMA, assert_all_called=False)
    router.post("/api/show").mock(side_effect=_show)
    router.get("/api/version").respond(json={"version": "0.34.0"})
    router.get("/api/ps").respond(json={"models": []})
    router.get("/api/tags").respond(json={"models": [
        {"name": "gemma3:4b", "size": 3338801804,
         "details": {"parameter_size": "4.3B", "quantization_level": "Q4_K_M"}},
        {"name": "nomic-embed-text", "size": 274302450, "details": {}},
    ]})
    return router


def make_settings(tmp_path: Path, **overrides) -> Settings:
    values = dict(
        _env_file=None, db_path=tmp_path / "test.db", admin_key=ADMIN_KEY, ollama_base_url=OLLAMA,
        warmup=False, global_concurrency=2, queue_timeout_s=0.3, stream_ping_interval_s=0.05,
        default_rpm=100, default_max_concurrent=2, failed_auth_per_ip_per_min=5, max_body_bytes=10_000,
        seed_models="gemma3:4b,nomic-embed-text", log_dir=tmp_path / "logs", health_interval_s=0,
        watch_services="",
    )
    values.update(overrides)
    return Settings(**values)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture
async def gw(settings, ollama):
    gateway = Gateway(settings, ollama_transport=httpx.MockTransport(ollama.async_handler))
    await gateway.start()
    yield gateway
    await gateway.stop()


@pytest.fixture
async def public(gw):
    transport = httpx.ASGITransport(app=create_public_app(gw))
    async with httpx.AsyncClient(transport=transport, base_url="http://public") as client:
        yield client


@pytest.fixture
async def admin(gw):
    transport = httpx.ASGITransport(app=create_admin_app(gw))
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8001",
                                 headers={"Authorization": f"Bearer {ADMIN_KEY}"}) as client:
        yield client


async def new_key(gw: Gateway, **limits) -> tuple[int, str]:
    key = generate_key()
    row = await gw.db.create_key("test", hash_key(key), display_prefix(key),
                                 limits.get("rpm_limit"), limits.get("max_concurrent"))
    return row["id"], key


@pytest.fixture
async def api_key(gw) -> str:
    return (await new_key(gw))[1]


@pytest.fixture
def auth(api_key) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"}


async def flush_usage(gw: Gateway) -> list[dict]:
    """Drain the async usage writer and return all rows, oldest first."""
    await gw.usage.stop()
    gw.usage.start()
    return list(reversed(await gw.db.recent_usage(1000)))

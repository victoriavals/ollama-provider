"""API key generation, lookup, and FastAPI auth dependencies."""

import hashlib
import hmac
import logging
import secrets
import time
from dataclasses import dataclass

from fastapi import Request

from .errors import APIError

log = logging.getLogger("gateway.auth")

KEY_PREFIX = "sk-"
CACHE_TTL_S = 30.0


def generate_key() -> str:
    return KEY_PREFIX + secrets.token_urlsafe(32)


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def display_prefix(key: str) -> str:
    return key[:10]


@dataclass(frozen=True)
class ApiKey:
    id: int
    name: str
    prefix: str
    rpm_limit: int | None
    max_concurrent: int | None


class KeyCache:
    """Short-lived cache of hash -> key so auth does not hit SQLite on every request."""

    def __init__(self, ttl: float = CACHE_TTL_S):
        self.ttl = ttl
        self._items: dict[str, tuple[float, ApiKey | None]] = {}

    def get(self, key_hash: str) -> tuple[bool, ApiKey | None]:
        hit = self._items.get(key_hash)
        if hit is None or hit[0] < time.monotonic():
            return False, None
        return True, hit[1]

    def put(self, key_hash: str, value: ApiKey | None) -> None:
        if len(self._items) > 10_000:
            self._items.clear()
        self._items[key_hash] = (time.monotonic() + self.ttl, value)

    def clear(self) -> None:
        self._items.clear()


def client_ip(request: Request) -> str:
    # Resolved once per request by RequestContextMiddleware (see CLIENT_IP_HEADERS).
    ip = getattr(request.state, "client_ip", None)
    return ip or (request.client.host if request.client else "unknown")


def _bearer(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


async def require_api_key(request: Request) -> ApiKey:
    gw = request.app.state.gw
    ip = client_ip(request)
    if gw.failed_auth.is_blocked(ip):
        raise APIError(429, "Too many failed authentication attempts. Try again later.",
                       code="auth_rate_limited", headers={"Retry-After": "60"})

    token = _bearer(request)
    key = await gw.lookup_key(token) if token else None
    if key is None:
        gw.failed_auth.record(ip)
        log.warning("auth failed ip=%s reason=%s", ip, "invalid_key" if token else "missing_key")
        message = ("Incorrect API key provided." if token
                   else "Missing API key. Send it as 'Authorization: Bearer <key>'.")
        raise APIError(401, message, code="invalid_api_key")
    access = getattr(request.state, "access", None)
    if access is not None:
        access.update(key_id=key.id, key=key.prefix, key_name=key.name)
    return key


async def require_admin(request: Request) -> None:
    admin_key = request.app.state.gw.settings.admin_key
    if not admin_key:
        raise APIError(503, "Admin API disabled: set ADMIN_KEY in .env", code="admin_disabled")
    token = _bearer(request) or ""
    if not hmac.compare_digest(token.encode(), admin_key.encode()):
        raise APIError(401, "Invalid admin key.", code="invalid_admin_key")

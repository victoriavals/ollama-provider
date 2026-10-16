import json
import logging
import secrets
import time
from datetime import datetime

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .logs import ACCESS_LOGGER

access_log = logging.getLogger(ACCESS_LOGGER)

# Successful health probes are not worth an access-log line each.
QUIET_PATHS = {"/health", "/health/ready"}


def scope_client_ip(scope: Scope, ip_headers: tuple[str, ...] = ("cf-connecting-ip",)) -> str:
    """Real client IP as reported by the tunnel in front of us (first configured header found).

    Port 8000 is reachable only by local processes (the tunnel agent), so these headers are
    trusted. For X-Forwarded-For the LAST entry is used: it is the one our single proxy hop
    appended; earlier entries come from the client and can be spoofed.
    """
    headers = {name.decode("latin-1").lower(): value.decode("latin-1")
               for name, value in scope.get("headers", [])}
    for name in ip_headers:
        value = headers.get(name)
        if value:
            ip = value.split(",")[-1].strip()
            if ip:
                return ip
    client = scope.get("client")
    return client[0] if client else "unknown"


class RequestContextMiddleware:
    """Assigns a request id and writes one JSON access-log line per request.

    Handlers enrich the line through `request.state.access` (key, model, tokens, error code,
    real outcome of a stream). The line is written after the response has fully finished,
    so streaming requests include their final token counts. Pure ASGI: never buffers streams.
    """

    def __init__(self, app: ASGIApp, app_name: str, ip_headers: tuple[str, ...] = ("cf-connecting-ip",)):
        self.app = app
        self.app_name = app_name
        self.ip_headers = ip_headers

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = "req_" + secrets.token_hex(12)
        access: dict = {}
        state = scope.setdefault("state", {})
        state["request_id"] = request_id
        state["access"] = access
        state["client_ip"] = client_ip = scope_client_ip(scope, self.ip_headers)
        started = time.monotonic()
        status = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message = {**message, "headers": [*message.get("headers", []),
                                                  (b"x-request-id", request_id.encode())]}
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            # Unhandled errors are turned into a 500 by ServerErrorMiddleware, which sits outside
            # this middleware, so the error handler cannot tag the access-log line itself.
            access.setdefault("error", "internal_error")
            raise
        finally:
            final_status = access.pop("status", status)
            # Skip noise: health probes and read-only admin polling (e.g. status.ps1 -Watch).
            quiet = scope["path"] in QUIET_PATHS or (self.app_name == "admin" and scope["method"] == "GET")
            if not (quiet and final_status < 400):
                entry = {
                    "ts": datetime.now().astimezone().isoformat(timespec="milliseconds"),
                    "app": self.app_name,
                    "request_id": request_id,
                    "ip": client_ip,
                    "method": scope["method"],
                    "path": scope["path"],
                    "status": final_status,
                    "duration_ms": int((time.monotonic() - started) * 1000),
                    **access,
                }
                access_log.info(json.dumps(entry, separators=(",", ":"), default=str))

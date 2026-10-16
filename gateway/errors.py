"""OpenAI-style error responses: {"error": {"message", "type", "param", "code"}}."""

import logging
import re

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("gateway")

_SECRET_RE = re.compile(r"sk-[A-Za-z0-9_\-]{6,}")

_STATUS_TYPES = {
    400: "invalid_request_error",
    401: "authentication_error",
    403: "permission_error",
    404: "not_found_error",
    405: "invalid_request_error",
    413: "invalid_request_error",
    429: "rate_limit_error",
}


def mask_secrets(text: str) -> str:
    return _SECRET_RE.sub("sk-***", text)


def error_body(message: str, type_: str, code: str | None = None, param: str | None = None) -> dict:
    return {"error": {"message": mask_secrets(message), "type": type_, "param": param, "code": code}}


class APIError(Exception):
    def __init__(
        self,
        status_code: int,
        message: str,
        type_: str | None = None,
        code: str | None = None,
        param: str | None = None,
        headers: dict[str, str] | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.type = type_ or _STATUS_TYPES.get(status_code, "server_error")
        self.code = code
        self.param = param
        self.headers = headers or {}

    def body(self) -> dict:
        return error_body(self.message, self.type, self.code, self.param)

    def response(self) -> JSONResponse:
        return JSONResponse(self.body(), status_code=self.status_code, headers=self.headers)


def _respond(request: Request, exc: APIError) -> JSONResponse:
    access = getattr(request.state, "access", None)
    if access is not None:
        access.setdefault("error", exc.code or exc.type)
    return exc.response()


def install_handlers(app: FastAPI) -> None:
    @app.exception_handler(APIError)
    async def _api_error(request: Request, exc: APIError) -> JSONResponse:
        return _respond(request, exc)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        message = exc.detail if isinstance(exc.detail, str) else "Request failed"
        return _respond(request, APIError(exc.status_code, message, headers=exc.headers))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        loc = ".".join(str(p) for p in first.get("loc", ()) if p != "body")
        message = f"{loc}: {first.get('msg', 'invalid request')}" if loc else first.get("msg", "invalid request")
        return _respond(request, APIError(400, message, param=loc or None))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error: %s", mask_secrets(repr(exc)))
        return _respond(request, APIError(500, "Internal server error", code="internal_error"))

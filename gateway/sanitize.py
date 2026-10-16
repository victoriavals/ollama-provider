"""Request allowlisting and validation before anything reaches Ollama.

Only known OpenAI fields are forwarded. Ollama-specific knobs (options, keep_alive,
num_ctx, think, ...) are dropped so a client cannot pin VRAM or blow up the context.
"""

import json
from typing import Any

from fastapi import Request

from .config import Settings
from .errors import APIError

_COMMON = {"model", "stream", "stream_options", "max_tokens", "temperature", "top_p", "stop",
           "seed", "presence_penalty", "frequency_penalty", "user"}
CHAT_FIELDS = _COMMON | {"messages", "response_format", "tools", "tool_choice"}
COMPLETION_FIELDS = _COMMON | {"prompt"}
EMBEDDING_FIELDS = {"model", "input", "encoding_format", "dimensions", "user"}

ROLES = {"system", "developer", "user", "assistant", "tool"}
NUMERIC = ("temperature", "top_p", "presence_penalty", "frequency_penalty")


def bad(message: str, param: str | None = None, code: str | None = None) -> APIError:
    return APIError(400, message, param=param, code=code)


async def read_json_body(request: Request, max_bytes: int) -> dict[str, Any]:
    too_large = APIError(413, f"Request body exceeds {max_bytes} bytes.", code="request_too_large")
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > max_bytes:
        raise too_large
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > max_bytes:
            raise too_large
        chunks.append(chunk)
    try:
        body = json.loads(b"".join(chunks) or b"null")
    except (ValueError, UnicodeDecodeError):
        raise bad("Request body is not valid JSON.") from None
    if not isinstance(body, dict):
        raise bad("Request body must be a JSON object.")
    return body


def require_model(body: dict[str, Any]) -> str:
    model = body.get("model")
    if not isinstance(model, str) or not model:
        raise bad("You must provide a model parameter.", param="model")
    return model


def model_not_found(model: str) -> APIError:
    return APIError(404, f"The model `{model}` does not exist or you do not have access to it.",
                    type_="invalid_request_error", code="model_not_found", param="model")


def _common(body: dict[str, Any], allowed: set[str], settings: Settings) -> tuple[dict[str, Any], bool]:
    if "n" in body and body["n"] not in (None, 1):
        raise bad("Only n=1 is supported.", param="n")
    if "max_completion_tokens" in body and "max_tokens" not in body:
        body["max_tokens"] = body["max_completion_tokens"]

    payload = {k: v for k, v in body.items() if k in allowed and v is not None}

    max_tokens = payload.get("max_tokens", settings.max_tokens_cap)
    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens < 1:
        raise bad("max_tokens must be a positive integer.", param="max_tokens")
    payload["max_tokens"] = min(max_tokens, settings.max_tokens_cap)

    for name in NUMERIC:
        if name in payload and (isinstance(payload[name], bool) or not isinstance(payload[name], int | float)):
            raise bad(f"{name} must be a number.", param=name)
    if "seed" in payload and (isinstance(payload["seed"], bool) or not isinstance(payload["seed"], int)):
        raise bad("seed must be an integer.", param="seed")
    if "user" in payload and (not isinstance(payload["user"], str) or len(payload["user"]) > 256):
        raise bad("user must be a string of at most 256 characters.", param="user")

    stop = payload.get("stop")
    if stop is not None:
        stops = [stop] if isinstance(stop, str) else stop
        if (not isinstance(stops, list) or len(stops) > 4
                or not all(isinstance(s, str) and 0 < len(s) <= 100 for s in stops)):
            raise bad("stop must be a string or up to 4 strings of at most 100 characters.", param="stop")

    stream = payload.get("stream", False)
    if not isinstance(stream, bool):
        raise bad("stream must be a boolean.", param="stream")
    include_usage = False
    if stream:
        opts = payload.get("stream_options") or {}
        if not isinstance(opts, dict):
            raise bad("stream_options must be an object.", param="stream_options")
        include_usage = bool(opts.get("include_usage"))
        # Always ask Ollama for usage so it can be logged; proxy strips it if the client did not ask.
        payload["stream_options"] = {"include_usage": True}
    else:
        payload.pop("stream_options", None)
        payload["stream"] = False
    return payload, include_usage


def sanitize_chat(body: dict[str, Any], caps: set[str], settings: Settings) -> tuple[dict[str, Any], bool]:
    if "completion" not in caps:
        raise bad(f"Model `{body['model']}` does not support chat completions.", param="model")
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        raise bad("messages must be a non-empty array.", param="messages")
    if len(messages) > settings.max_messages:
        raise bad(f"Too many messages (max {settings.max_messages}).", param="messages")

    images = 0
    for i, msg in enumerate(messages):
        if not isinstance(msg, dict) or msg.get("role") not in ROLES:
            raise bad(f"messages[{i}] must be an object with a valid role.", param=f"messages[{i}].role")
        content = msg.get("content")
        if isinstance(content, list):
            for part in content:
                if not isinstance(part, dict) or not isinstance(part.get("type"), str):
                    raise bad(f"messages[{i}].content has an invalid part.", param=f"messages[{i}].content")
                if part["type"] == "image_url":
                    # Only inline base64 images: never let the server fetch a URL (SSRF), even if a
                    # future Ollama version starts downloading http(s) image URLs.
                    image = part.get("image_url")
                    url = image.get("url") if isinstance(image, dict) else image
                    if not isinstance(url, str) or not url.startswith("data:image/") or ";base64," not in url:
                        raise bad("image_url must be a base64 data URL (data:image/...;base64,...).",
                                  param=f"messages[{i}].content")
                    images += 1
                elif part["type"] != "text":
                    raise bad(f"Content part type `{part['type']}` is not supported.",
                              param=f"messages[{i}].content")
        elif content is not None and not isinstance(content, str):
            raise bad(f"messages[{i}].content must be a string or an array.", param=f"messages[{i}].content")

    if images and "vision" not in caps:
        raise bad(f"Model `{body['model']}` does not accept images.", param="messages")
    if images > settings.max_images:
        raise bad(f"Too many images (max {settings.max_images} per request).", param="messages")
    if (body.get("tools") or body.get("tool_choice")) and "tools" not in caps:
        raise bad(f"Model `{body['model']}` does not support tools.", param="tools")
    if body.get("response_format") is not None and not isinstance(body["response_format"], dict):
        raise bad("response_format must be an object.", param="response_format")

    return _common(body, CHAT_FIELDS, settings)


def sanitize_completion(body: dict[str, Any], caps: set[str], settings: Settings) -> tuple[dict[str, Any], bool]:
    if "completion" not in caps:
        raise bad(f"Model `{body['model']}` does not support completions.", param="model")
    if not isinstance(body.get("prompt"), str) or not body["prompt"]:
        raise bad("prompt must be a non-empty string.", param="prompt")
    return _common(body, COMPLETION_FIELDS, settings)


def sanitize_embeddings(body: dict[str, Any], caps: set[str], settings: Settings) -> dict[str, Any]:
    if "embedding" not in caps:
        raise bad(f"Model `{body['model']}` does not support embeddings.", param="model",
                  code="model_not_supported")
    inp = body.get("input")
    items = [inp] if isinstance(inp, str) else inp
    if not isinstance(items, list) or not items or not all(isinstance(s, str) and s for s in items):
        raise bad("input must be a non-empty string or array of non-empty strings.", param="input")
    if len(items) > settings.max_embedding_inputs:
        raise bad(f"Too many inputs (max {settings.max_embedding_inputs}).", param="input")
    return {k: v for k, v in body.items() if k in EMBEDDING_FIELDS and v is not None}

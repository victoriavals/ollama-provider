"""Prompt & response audit log: what each API key sent and got back (table prompt_logs, admin only).

Prompt and response text are each capped at PROMPT_LOG_MAX_CHARS, images are replaced by a size
note (the base64 data is never stored), and anything that looks like an API key is masked.
Content is only ever stored in the SQLite table under data/ (ACL-restricted), never in the text
log files.
"""

from typing import Any

from .db import PromptRecord
from .errors import mask_secrets

PREVIEW_CHARS = 200
END_USER_CHARS = 200
IMAGE_TOKENS = 256  # rough prompt cost of one image (gemma3), used by estimate_prompt_tokens


def _image_note(part: dict[str, Any]) -> str:
    image = part.get("image_url")
    url = image.get("url", "") if isinstance(image, dict) else str(image or "")
    return f"[gambar ~{len(url) * 3 // 4 // 1024} KB]"


def _text(content: Any) -> str:
    """Plain text of an OpenAI message content (string or list of parts)."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for p in content:
            if not isinstance(p, dict):
                parts.append(str(p))
            elif p.get("type") == "text":
                parts.append(str(p.get("text", "")))
            elif p.get("type") == "image_url":
                parts.append(_image_note(p))
            else:
                parts.append(f"[{p.get('type', 'part')}]")
        return "\n".join(parts)
    return str(content)


def _tool_call_text(name: Any, arguments: Any) -> str:
    return f"[tool call] {name or '?'}({arguments or ''})"


def _function(item: Any) -> dict[str, Any]:
    fn = item.get("function") if isinstance(item, dict) else None
    return fn if isinstance(fn, dict) else {}


def _preview(text: str) -> str:
    return mask_secrets(" ".join(text.split()))[:PREVIEW_CHARS]


class _Budget:
    """Caps the total text kept for one prompt; whatever does not fit is replaced by a note."""

    def __init__(self, limit: int):
        self.left = limit

    def take(self, text: str) -> str:
        text = mask_secrets(text)
        if len(text) <= self.left:
            self.left -= len(text)
            return text
        kept, self.left = text[:self.left], 0
        return f"{kept}… [dipotong {len(text) - len(kept)} karakter]"


def capture_prompt(body: dict[str, Any], endpoint: str, max_chars: int) -> tuple[list[dict[str, str]], str, int]:
    """(messages to store, preview of the last user turn, original text length)."""
    if endpoint == "chat":
        turns = []
        tools = body.get("tools")
        if isinstance(tools, list) and tools:
            turns.append(("tools", ", ".join(str(_function(t).get("name", "?")) for t in tools)))
        messages = body.get("messages")
        for m in messages if isinstance(messages, list) else []:
            if not isinstance(m, dict):
                continue
            calls = m.get("tool_calls")
            calls = [_tool_call_text(_function(c).get("name"), _function(c).get("arguments"))
                     for c in (calls if isinstance(calls, list) else [])]
            turns.append((str(m.get("role", "?")), "\n".join([_text(m.get("content")), *calls]).strip()))
    elif endpoint == "completions":
        turns = [("prompt", _text(body.get("prompt")))]
    else:
        value = body.get("input")
        turns = [("input", "\n".join(map(str, value)) if isinstance(value, list) else _text(value))]
    budget = _Budget(max_chars)
    messages = [{"role": role, "content": budget.take(text)} for role, text in turns]
    asked = [text for role, text in turns if role in ("user", "prompt", "input")]
    return messages, _preview(asked[-1] if asked else (turns[-1][1] if turns else "")), sum(len(t) for _, t in turns)


def estimate_prompt_tokens(payload: dict[str, Any]) -> int:
    """Rough prompt size (~4 characters per token, a fixed cost per image) for streams that end
    before Ollama reports usage, so a client cannot dodge its quota by disconnecting."""
    chars = images = 0
    for m in payload.get("messages") or []:
        content = m.get("content") if isinstance(m, dict) else None
        if isinstance(content, list):
            images += sum(1 for p in content if isinstance(p, dict) and p.get("type") == "image_url")
        chars += len(_text([p for p in content if not (isinstance(p, dict) and p.get("type") == "image_url")])
                     if isinstance(content, list) else _text(content))
    for field in ("prompt", "input"):
        value = payload.get(field)
        chars += len("\n".join(map(str, value)) if isinstance(value, list) else _text(value))
    return chars // 4 + 1 + images * IMAGE_TOKENS


def placeholder_record(body: Any, endpoint: str, key: Any, model: str, request_id: str, client_ip: str) -> PromptRecord:
    """Kept when capture fails, so the request still shows up in the audit log."""
    note = "[prompt tidak dapat dibaca untuk log]"
    return PromptRecord(key_id=key.id, key_name=key.name, endpoint=endpoint, model=model,
                        prompt=[{"role": "?", "content": note}], prompt_preview=note, prompt_chars=0,
                        request_id=request_id, client_ip=client_ip,
                        stream=bool(body.get("stream")) if isinstance(body, dict) else False)


def new_record(body: dict[str, Any], endpoint: str, key: Any, model: str, request_id: str, client_ip: str,
               max_chars: int) -> PromptRecord:
    messages, preview, chars = capture_prompt(body, endpoint, max_chars)
    user = body.get("user")
    return PromptRecord(
        key_id=key.id, key_name=key.name, endpoint=endpoint, model=model, prompt=messages, prompt_preview=preview,
        prompt_chars=chars, request_id=request_id, client_ip=client_ip, stream=bool(body.get("stream")),
        end_user=mask_secrets(str(user))[:END_USER_CHARS] if isinstance(user, (str, int)) and str(user) else None,
    )


class ResponseCollector:
    """Collects the model's answer (text + tool calls) from a JSON response or SSE chunks, capped."""

    def __init__(self, max_chars: int):
        self.max_chars = max_chars
        self._parts: list[str] = []
        self._len = 0
        self._dropped = 0
        self._tools: dict[Any, list[str]] = {}  # stream: tool calls arrive in pieces, keyed by id/index
        self._last_id: dict[Any, str] = {}

    def _add(self, text: Any) -> None:
        if not text:
            return
        text = str(text)
        room = self.max_chars - self._len
        if room < len(text):
            self._dropped += len(text) - max(room, 0)
            text = text[:max(room, 0)]
        if text:
            self._parts.append(text)
            self._len += len(text)

    def add_json(self, data: Any) -> None:
        if not isinstance(data, dict):
            return
        if data.get("object") == "list" and isinstance(data.get("data"), list):  # embeddings
            self._add(f"[{len(data['data'])} embedding]")
            return
        for choice in data.get("choices") or []:
            if not isinstance(choice, dict):
                continue
            message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
            self._add(message.get("content") or choice.get("text"))
            calls = message.get("tool_calls")
            for call in calls if isinstance(calls, list) else []:
                fn = _function(call)
                self._add(("\n" if self._len else "") + _tool_call_text(fn.get("name"), fn.get("arguments")))

    def add_chunk(self, chunk: dict[str, Any]) -> None:
        for choice in chunk.get("choices") or []:
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
            self._add(delta.get("content") or choice.get("text"))
            calls = delta.get("tool_calls")
            for call in calls if isinstance(calls, list) else []:
                if not isinstance(call, dict):
                    continue
                fn = _function(call)
                # A call is continued by chunks with the same index; a new id starts a new call.
                ident = call.get("id") or self._last_id.get(call.get("index", 0)) or call.get("index", len(self._tools))
                if call.get("id"):
                    self._last_id[call.get("index", 0)] = call["id"]
                slot = self._tools.setdefault(ident, ["", ""])
                slot[0] += str(fn.get("name") or "")
                if len(slot[1]) < self.max_chars:
                    slot[1] += str(fn.get("arguments") or "")

    def text(self) -> str | None:
        for name, arguments in self._tools.values():
            self._add(("\n" if self._len else "") + _tool_call_text(name, arguments))
        self._tools.clear()
        out = "".join(self._parts)
        if self._dropped:
            out += f"… [dipotong {self._dropped} karakter]"
        return mask_secrets(out) or None

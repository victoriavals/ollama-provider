import pytest

from gateway.config import Settings
from gateway.errors import APIError
from gateway.sanitize import sanitize_chat, sanitize_completion, sanitize_embeddings

S = Settings(_env_file=None, max_tokens_cap=1000, max_images=2)
CHAT_CAPS = {"completion", "vision"}


def chat(**extra):
    return {"model": "gemma3:4b", "messages": [{"role": "user", "content": "hi"}], **extra}


def test_drops_ollama_knobs_and_unknown_fields():
    payload, _ = sanitize_chat(chat(options={"num_ctx": 131072}, keep_alive=-1, num_ctx=99999,
                                    think=True, foo="bar", temperature=0.2), CHAT_CAPS, S)
    assert set(payload) == {"model", "messages", "temperature", "max_tokens", "stream"}
    assert payload["temperature"] == 0.2


def test_max_tokens_default_and_cap():
    assert sanitize_chat(chat(), CHAT_CAPS, S)[0]["max_tokens"] == 1000
    assert sanitize_chat(chat(max_tokens=50), CHAT_CAPS, S)[0]["max_tokens"] == 50
    assert sanitize_chat(chat(max_tokens=99999), CHAT_CAPS, S)[0]["max_tokens"] == 1000
    assert sanitize_chat(chat(max_completion_tokens=70), CHAT_CAPS, S)[0]["max_tokens"] == 70


@pytest.mark.parametrize("bad", [
    {"n": 2},
    {"max_tokens": 0},
    {"max_tokens": "10"},
    {"temperature": "hot"},
    {"stop": ["a", "b", "c", "d", "e"]},
    {"stream": "yes"},
    {"messages": []},
    {"messages": [{"role": "robot", "content": "x"}]},
    {"messages": [{"role": "user", "content": [{"type": "input_audio"}]}]},
    {"tools": [{"type": "function", "function": {"name": "f"}}]},
])
def test_rejects_invalid_requests(bad):
    with pytest.raises(APIError) as exc:
        sanitize_chat(chat(**bad), CHAT_CAPS, S)
    assert exc.value.status_code == 400


def test_stream_always_requests_usage_upstream():
    payload, include = sanitize_chat(chat(stream=True), CHAT_CAPS, S)
    assert payload["stream_options"] == {"include_usage": True} and include is False
    payload, include = sanitize_chat(chat(stream=True, stream_options={"include_usage": True}), CHAT_CAPS, S)
    assert include is True


def test_non_stream_drops_stream_options():
    payload, _ = sanitize_chat(chat(stream_options={"include_usage": True}), CHAT_CAPS, S)
    assert "stream_options" not in payload and payload["stream"] is False


def _image_msg(count):
    parts = [{"type": "text", "text": "what is this"}]
    parts += [{"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}] * count
    return {"messages": [{"role": "user", "content": parts}]}


def test_images_need_vision_and_are_capped():
    sanitize_chat(chat(**_image_msg(2)), CHAT_CAPS, S)
    with pytest.raises(APIError):
        sanitize_chat(chat(**_image_msg(3)), CHAT_CAPS, S)
    with pytest.raises(APIError):
        sanitize_chat(chat(**_image_msg(1)), {"completion"}, S)


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:11434/api/tags",
    "https://example.com/cat.png",
    "file:///C:/Windows/win.ini",
    "data:text/plain;base64,AAAA",
    "data:image/png,not-base64",
])
def test_image_url_must_be_inline_base64(url):
    body = chat(messages=[{"role": "user", "content": [{"type": "image_url", "image_url": {"url": url}}]}])
    with pytest.raises(APIError) as exc:
        sanitize_chat(body, CHAT_CAPS, S)
    assert exc.value.status_code == 400


def test_tools_allowed_when_model_supports_them():
    payload, _ = sanitize_chat(chat(tools=[{"type": "function", "function": {"name": "f"}}]),
                               {"completion", "tools"}, S)
    assert "tools" in payload


def test_completion_requires_string_prompt():
    payload, _ = sanitize_completion({"model": "m", "prompt": "The sky is", "echo": True}, {"completion"}, S)
    assert payload["prompt"] == "The sky is" and "echo" not in payload
    with pytest.raises(APIError):
        sanitize_completion({"model": "m", "prompt": ["a", "b"]}, {"completion"}, S)


def test_embeddings_need_capability():
    with pytest.raises(APIError) as exc:
        sanitize_embeddings({"model": "gemma3:4b", "input": "x"}, CHAT_CAPS, S)
    assert exc.value.code == "model_not_supported"
    payload = sanitize_embeddings({"model": "e", "input": ["a", "b"], "keep_alive": -1}, {"embedding"}, S)
    assert payload == {"model": "e", "input": ["a", "b"]}


THINKING_CAPS = {"completion", "vision", "tools", "thinking"}


def test_thinking_model_gets_default_reasoning_effort():
    payload, _ = sanitize_chat(chat(), THINKING_CAPS, S)
    assert payload["reasoning_effort"] == "none"  # default keeps answers from being eaten by thinking
    payload, _ = sanitize_chat(chat(reasoning_effort="high"), THINKING_CAPS, S)
    assert payload["reasoning_effort"] == "high"  # explicit client choice wins
    model_default = Settings(_env_file=None, default_reasoning_effort="")
    payload, _ = sanitize_chat(chat(), THINKING_CAPS, model_default)
    assert "reasoning_effort" not in payload  # "" = leave it to the model


def test_reasoning_effort_validated_and_dropped_for_non_thinking_models():
    with pytest.raises(APIError) as exc:
        sanitize_chat(chat(reasoning_effort="banana"), THINKING_CAPS, S)
    assert exc.value.param == "reasoning_effort"
    with pytest.raises(APIError):  # "low" runs away to max_tokens on qwen3.5:4b, so it is refused
        sanitize_chat(chat(reasoning_effort="low"), THINKING_CAPS, S)
    payload, _ = sanitize_chat(chat(reasoning_effort="high"), CHAT_CAPS, S)
    assert "reasoning_effort" not in payload


def test_completions_rejected_for_thinking_models():
    with pytest.raises(APIError) as exc:
        sanitize_completion({"model": "qwen3.5:4b", "prompt": "Halo"}, THINKING_CAPS, S)
    assert exc.value.code == "model_not_supported" and "chat/completions" in exc.value.message


def test_invalid_default_reasoning_effort_setting():
    with pytest.raises(ValueError):
        Settings(_env_file=None, default_reasoning_effort="max")

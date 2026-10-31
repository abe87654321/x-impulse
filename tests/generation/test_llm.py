"""OpenAI 兼容模型适配器 —— 请求构造与响应解析。"""

from __future__ import annotations

import json
import urllib.error

import pytest

from kb.generation.citations import build_prompt
from kb.generation.llm import (
    GenerationError,
    LLMConfig,
    OpenAICompatibleGenerator,
)
from kb.retrieval.models import RetrievedChunk
from tests.retrieval.fakes import make_record


def _evidence() -> list[RetrievedChunk]:
    return [RetrievedChunk(record=make_record("c1"), score=1.0)]


class _Capture:
    def __init__(self, response: object) -> None:
        self.response = response
        self.url = ""
        self.headers: dict[str, str] = {}
        self.body = b""
        self.timeout = 0.0

    def __call__(self, url, headers, body, timeout):
        self.url = url
        self.headers = headers
        self.body = body
        self.timeout = timeout
        return self.response


async def test_builds_openai_compatible_request():
    capture = _Capture({"choices": [{"message": {"content": "事实[1]。"}}]})
    generator = OpenAICompatibleGenerator(
        LLMConfig(
            base_url="http://llm.local/v1/",
            model="qwen",
            api_key="secret",
            timeout_sec=5,
        ),
        transport=capture,
    )

    content = await generator.generate("报销标准是什么？", _evidence())

    assert content == "事实[1]。"
    assert capture.url == "http://llm.local/v1/chat/completions"
    assert capture.headers["Authorization"] == "Bearer secret"
    assert capture.timeout == 5
    payload = json.loads(capture.body)
    assert payload["model"] == "qwen"
    assert payload["messages"][0]["content"] == build_prompt("报销标准是什么？", _evidence())


async def test_omits_authorization_without_api_key():
    capture = _Capture({"choices": [{"message": {"content": "x[1]。"}}]})
    generator = OpenAICompatibleGenerator(transport=capture)

    await generator.generate("q", _evidence())

    assert "Authorization" not in capture.headers


async def test_invalid_response_raises_generation_error():
    generator = OpenAICompatibleGenerator(transport=_Capture({"choices": []}))

    with pytest.raises(GenerationError):
        await generator.generate("q", _evidence())


async def test_transport_failure_is_wrapped():
    def boom(url, headers, body, timeout):
        raise urllib.error.URLError("connection refused")

    generator = OpenAICompatibleGenerator(transport=boom)

    with pytest.raises(GenerationError):
        await generator.generate("q", _evidence())


async def test_empty_content_raises_generation_error():
    generator = OpenAICompatibleGenerator(
        transport=_Capture({"choices": [{"message": {"content": "   "}}]})
    )

    with pytest.raises(GenerationError):
        await generator.generate("q", _evidence())


class _StreamCapture:
    def __init__(self, items: list[object]) -> None:
        self.items = items
        self.body: bytes = b""

    def __call__(self, url, headers, body, timeout):
        self.body = body
        return iter(self.items)


def _delta(content: str) -> dict:
    return {"choices": [{"delta": {"content": content}}]}


async def test_streams_incremental_content():
    capture = _StreamCapture([_delta("事实"), _delta("句"), _delta("[1]。")])
    generator = OpenAICompatibleGenerator(stream_transport=capture)

    parts = [part async for part in generator.generate_stream("q", _evidence())]

    assert "".join(parts) == "事实句[1]。"
    assert json.loads(capture.body)["stream"] is True


async def test_stream_ignores_empty_deltas_and_reasoning():
    capture = _StreamCapture(
        [
            {"choices": [{"delta": {"role": "assistant"}}]},
            _delta(""),
            _delta("内容[1]。"),
            {"choices": []},
        ]
    )
    generator = OpenAICompatibleGenerator(stream_transport=capture)

    parts = [part async for part in generator.generate_stream("q", _evidence())]

    assert parts == ["内容[1]。"]

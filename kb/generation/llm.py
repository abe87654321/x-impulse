"""OpenAI 兼容的模型适配器（FR-05-15 的可插拔入口）。

只调用 /chat/completions，不绑定具体厂商。传输层默认用标准库 urllib，
以便零额外依赖先跑通；生产可注入带重试/连接池的传输实现。

注意：商用 API 默认数据出域。零外部依赖模式下必须由部署层禁用本适配器，
不得在运行时静默回退（I-10 / FR-06-08）。
"""

from __future__ import annotations

import asyncio
import json
import os
import urllib.error
import urllib.request
from collections.abc import AsyncIterator, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from kb.generation.citations import Generator, build_prompt
from kb.generation.extractive import ExtractiveGenerator
from kb.retrieval.models import RetrievedChunk


class GenerationError(Exception):
    """模型调用失败或返回结构不合法。"""


class GeneratorConfigError(Exception):
    """生成后端缺失或配置非法。禁止静默回退到更弱的实现（I-10）。"""


class JsonTransport(Protocol):
    def __call__(
        self, url: str, headers: dict[str, str], body: bytes, timeout: float
    ) -> object: ...


class StreamTransport(Protocol):
    """返回 SSE data 行解析后的对象迭代器；测试可注入静态序列。"""

    def __call__(
        self, url: str, headers: dict[str, str], body: bytes, timeout: float
    ) -> Iterator[object]: ...


_SENTINEL = object()


@dataclass(frozen=True)
class LLMConfig:
    base_url: str = "http://localhost:8000/v1"
    model: str = "local"
    api_key: str = ""
    timeout_sec: float = 30.0

    @property
    def endpoint(self) -> str:
        return f"{self.base_url.rstrip('/')}/chat/completions"


class OpenAICompatibleGenerator:
    def __init__(
        self,
        config: LLMConfig | None = None,
        *,
        transport: JsonTransport | None = None,
        stream_transport: StreamTransport | None = None,
    ) -> None:
        self._config = config or LLMConfig()
        self._transport = transport or _urllib_transport
        self._stream_transport = stream_transport

    def _request(
        self, question: str, evidence: Sequence[RetrievedChunk], *, stream: bool
    ) -> tuple[dict[str, str], bytes]:
        prompt = build_prompt(question, evidence)
        payload: dict[str, object] = {
            "model": self._config.model,
            "temperature": 0,
            "messages": [{"role": "user", "content": prompt}],
        }
        if stream:
            payload["stream"] = True
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json; charset=utf-8"}
        if self._config.api_key:
            headers["Authorization"] = f"Bearer {self._config.api_key}"
        return headers, body

    async def generate(self, question: str, evidence: Sequence[RetrievedChunk]) -> str:
        headers, body = self._request(question, evidence, stream=False)
        try:
            response = await asyncio.to_thread(
                self._transport,
                self._config.endpoint,
                headers,
                body,
                self._config.timeout_sec,
            )
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise GenerationError(f"模型调用失败：{exc}") from exc

        content = _extract_content(response)
        if not content.strip():
            raise GenerationError("模型返回空内容")
        return content

    async def generate_stream(
        self, question: str, evidence: Sequence[RetrievedChunk]
    ) -> AsyncIterator[str]:
        """逐 token 转发模型增量输出，供引用绑定逐句校验（FR-05-12 / FR-05-05a）。"""
        headers, body = self._request(question, evidence, stream=True)
        if self._stream_transport is not None:
            iterator = iter(
                self._stream_transport(
                    self._config.endpoint, headers, body, self._config.timeout_sec
                )
            )
        else:
            iterator = _sse_items(self._config.endpoint, headers, body, self._config.timeout_sec)
        try:
            while True:
                try:
                    item = await asyncio.to_thread(_advance, iterator)
                except (urllib.error.URLError, TimeoutError, OSError) as exc:
                    raise GenerationError(f"模型流式调用失败：{exc}") from exc
                if item is _SENTINEL:
                    return
                content = _extract_delta(item)
                if content:
                    yield content
        finally:
            close = getattr(iterator, "close", None)
            if close is not None:
                await asyncio.to_thread(close)


def build_generator_from_env(environ: Mapping[str, str] | None = None) -> Generator:
    """按 `KB_GENERATOR` 选择生成后端。未配置或未知值一律报错。

    取值：`openai`（任意 OpenAI 兼容端点）或 `extractive`（仅演示/离线回归）。
    """
    env = os.environ if environ is None else environ
    provider = env.get("KB_GENERATOR", "").strip().lower()
    if not provider:
        raise GeneratorConfigError("未配置 KB_GENERATOR，拒绝隐式选择生成后端")
    if provider == "extractive":
        return ExtractiveGenerator(max_evidence=_positive_int(env, "KB_EXTRACTIVE_MAX_EVIDENCE", 3))
    if provider in {"openai", "openai-compatible", "openai_compatible"}:
        base_url = env.get("KB_LLM_BASE_URL", "").strip()
        if not base_url:
            raise GeneratorConfigError("KB_LLM_BASE_URL 必填")
        return OpenAICompatibleGenerator(
            LLMConfig(
                base_url=base_url,
                model=env.get("KB_LLM_MODEL", "local"),
                api_key=env.get("KB_LLM_API_KEY", ""),
                timeout_sec=_positive_float(env, "KB_LLM_TIMEOUT_SEC", 30.0),
            )
        )
    raise GeneratorConfigError(f"未知 KB_GENERATOR：{provider}")


def _positive_int(env: Mapping[str, str], key: str, default: int) -> int:
    raw = env.get(key)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise GeneratorConfigError(f"{key} 不是整数") from exc
    if value <= 0:
        raise GeneratorConfigError(f"{key} 必须 > 0")
    return value


def _positive_float(env: Mapping[str, str], key: str, default: float) -> float:
    raw = env.get(key)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise GeneratorConfigError(f"{key} 不是数字") from exc
    if value <= 0:
        raise GeneratorConfigError(f"{key} 必须 > 0")
    return value


def _advance(iterator: Iterator[object]) -> object:
    try:
        return next(iterator)
    except StopIteration:
        return _SENTINEL


def _extract_delta(item: object) -> str:
    if not isinstance(item, dict):
        return ""
    choices = item.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""
    delta = first.get("delta")
    if not isinstance(delta, dict):
        return ""
    content = delta.get("content")
    return content if isinstance(content, str) else ""


def _sse_items(url: str, headers: dict[str, str], body: bytes, timeout: float) -> Iterator[object]:
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")  # noqa: S310
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        for raw in response:
            line = raw.decode("utf-8").strip()
            if not line.startswith("data:"):
                continue
            data = line[len("data:") :].strip()
            if data == "[DONE]":
                return
            try:
                parsed: object = json.loads(data)
            except json.JSONDecodeError:
                continue
            yield parsed


def _extract_content(response: object) -> str:
    if not isinstance(response, dict):
        raise GenerationError("模型返回不是 JSON 对象")
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        raise GenerationError("模型返回缺少 choices")
    first = choices[0]
    if not isinstance(first, dict):
        raise GenerationError("choices[0] 不是对象")
    message = first.get("message")
    if not isinstance(message, dict):
        raise GenerationError("choices[0].message 不是对象")
    content = message.get("content")
    if not isinstance(content, str):
        raise GenerationError("choices[0].message.content 不是字符串")
    return content


def _urllib_transport(url: str, headers: dict[str, str], body: bytes, timeout: float) -> object:
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")  # noqa: S310
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        raw = response.read()
    parsed: object = json.loads(raw)
    return parsed


__all__ = [
    "GenerationError",
    "GeneratorConfigError",
    "JsonTransport",
    "LLMConfig",
    "OpenAICompatibleGenerator",
    "StreamTransport",
    "build_generator_from_env",
]

"""OpenAI 兼容的模型适配器（FR-05-15 的可插拔入口）。

只调用 /chat/completions，不绑定具体厂商。传输层默认用标准库 urllib，
以便零额外依赖先跑通；生产可注入带重试/连接池的传输实现。

注意：商用 API 默认数据出域。零外部依赖模式下必须由部署层禁用本适配器，
不得在运行时静默回退（I-10 / FR-06-08）。
"""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from kb.generation.citations import build_prompt
from kb.retrieval.models import RetrievedChunk


class GenerationError(Exception):
    """模型调用失败或返回结构不合法。"""


class JsonTransport(Protocol):
    def __call__(
        self, url: str, headers: dict[str, str], body: bytes, timeout: float
    ) -> object: ...


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
        self, config: LLMConfig | None = None, *, transport: JsonTransport | None = None
    ) -> None:
        self._config = config or LLMConfig()
        self._transport = transport or _urllib_transport

    async def generate(self, question: str, evidence: Sequence[RetrievedChunk]) -> str:
        prompt = build_prompt(question, evidence)
        payload: dict[str, object] = {
            "model": self._config.model,
            "temperature": 0,
            "messages": [{"role": "user", "content": prompt}],
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json; charset=utf-8"}
        if self._config.api_key:
            headers["Authorization"] = f"Bearer {self._config.api_key}"

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
    "JsonTransport",
    "LLMConfig",
    "OpenAICompatibleGenerator",
]

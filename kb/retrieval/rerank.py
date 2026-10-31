"""Cross-Encoder 重排 —— FR-04-08。

能力是 P0，但**默认关闭**。是否启用唯一取决于 FR-04-17 在客户评测集上
测出的 recall@10 / recall@50 差值，以及与关闭重排的对照提升。弱重排器
会把已召回的正确证据往下压，因此不得以"业界标准做法"为由默认开启。

重排只接收已完成权限二次校验的候选，避免无权内容进入模型上下文。
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping, Sequence
from typing import Protocol

from kb.retrieval.models import ScoredChunk


class RerankConfigError(Exception):
    """重排器缺失或配置非法。禁止静默降级为无重排。"""


class Reranker(Protocol):
    model_version: str

    async def rerank(
        self, query: str, candidates: Sequence[ScoredChunk], *, limit: int
    ) -> list[ScoredChunk]: ...


class CrossEncoderReranker:
    """sentence-transformers CrossEncoder 适配器；依赖 `.[ml]` 可选组。"""

    def __init__(self, model: str, *, device: str | None = None) -> None:
        if not model.strip():
            raise RerankConfigError("重排模型名不能为空")
        try:
            from sentence_transformers import CrossEncoder
        except ImportError as exc:  # pragma: no cover - 环境缺依赖时触发
            raise RerankConfigError(
                "未安装 sentence-transformers；请安装 `.[ml]` 或改用其他重排后端"
            ) from exc
        self._model = CrossEncoder(model, device=device)
        self.model_version = model

    async def rerank(
        self, query: str, candidates: Sequence[ScoredChunk], *, limit: int
    ) -> list[ScoredChunk]:
        if not candidates:
            return []
        pairs = [(query, candidate.record.content) for candidate in candidates]
        scores = await asyncio.to_thread(self._model.predict, pairs)
        ranked = sorted(
            zip(candidates, scores, strict=True),
            key=lambda item: float(item[1]),
            reverse=True,
        )
        return [
            ScoredChunk(record=candidate.record, score=float(score))
            for candidate, score in ranked[:limit]
        ]


def build_reranker_from_env(environ: Mapping[str, str] | None = None) -> Reranker | None:
    """未显式开启时返回 None；开启但配置不全则报错，不隐式降级。"""
    env = os.environ if environ is None else environ
    if not _truthy(env.get("KB_RERANK_ENABLED")):
        return None
    provider = env.get("KB_RERANKER", "cross-encoder").strip().lower()
    if provider in {"cross-encoder", "cross_encoder", "crossencoder", "st"}:
        model = env.get("KB_RERANK_MODEL", "").strip()
        if not model:
            raise RerankConfigError("KB_RERANK_MODEL 必填")
        return CrossEncoderReranker(model, device=env.get("KB_RERANK_DEVICE") or None)
    raise RerankConfigError(f"未知 KB_RERANKER：{provider}")


def _truthy(value: str | None) -> bool:
    return value is not None and value.strip().lower() in {"1", "true", "yes", "on"}


__all__ = [
    "CrossEncoderReranker",
    "RerankConfigError",
    "Reranker",
    "build_reranker_from_env",
]

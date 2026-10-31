"""端到端答案编排：检索 → 模型 → 逐句引用绑定 → 答案/拒答。

检索与生成都是可注入的，便于替换为真实后端而不改编排逻辑。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Protocol

from kb.generation.citations import (
    DEFAULT_REFUSAL,
    Answer,
    CitationBinder,
    Generator,
    StreamEvent,
)
from kb.identity.permissions import DelegationContext
from kb.retrieval.models import RetrievedChunk
from kb.retrieval.pg_adapters import HybridRetrievalService
from kb.retrieval.service import RetrievalService


class EvidenceRetriever(Protocol):
    async def search(self, ctx: DelegationContext, query: str) -> Sequence[RetrievedChunk]: ...


class RetrievalServiceAdapter:
    """把持久层检索结果转成生成层需要的证据。"""

    def __init__(self, service: RetrievalService) -> None:
        self._service = service

    async def search(self, ctx: DelegationContext, query: str) -> Sequence[RetrievedChunk]:
        records = await asyncio.to_thread(self._service.search_chunks, ctx, query)
        return [RetrievedChunk(record=record, score=1.0) for record in records]


class HybridRetrievalAdapter:
    def __init__(self, service: HybridRetrievalService) -> None:
        self._service = service

    async def search(self, ctx: DelegationContext, query: str) -> Sequence[RetrievedChunk]:
        return await self._service.retrieve(ctx, query)


@dataclass(frozen=True)
class AnswerResult:
    answer: Answer
    evidence: tuple[RetrievedChunk, ...]


@dataclass(frozen=True)
class PreparedStream:
    evidence: tuple[RetrievedChunk, ...]
    events: AsyncIterator[StreamEvent]


class AnswerService:
    def __init__(
        self,
        retriever: EvidenceRetriever,
        generator: Generator,
        *,
        refusal_text: str = DEFAULT_REFUSAL,
    ) -> None:
        self._retriever = retriever
        self._generator = generator
        self._refusal_text = refusal_text

    async def answer(self, ctx: DelegationContext, question: str) -> Answer:
        result = await self.answer_with_evidence(ctx, question)
        return result.answer

    async def answer_with_evidence(self, ctx: DelegationContext, question: str) -> AnswerResult:
        """同 `answer`，但把召回证据一并带回，供调用方做抽取检测（FR-16-02）。"""
        evidence = tuple(await self._retriever.search(ctx, question))
        binder = CitationBinder(evidence, refusal_text=self._refusal_text)
        if not evidence:
            return AnswerResult(answer=binder.bind(""), evidence=evidence)
        raw = await self._generator.generate(question, evidence)
        return AnswerResult(answer=binder.bind(raw), evidence=evidence)

    async def prepare_stream(self, ctx: DelegationContext, question: str) -> PreparedStream:
        """先完成召回，再返回可与引用绑定并行的模型 token 流。"""
        evidence = tuple(await self._retriever.search(ctx, question))
        binder = CitationBinder(evidence, refusal_text=self._refusal_text)
        if not evidence:
            return PreparedStream(evidence=evidence, events=binder.bind_stream(_empty()))
        tokens = self._token_stream(question, evidence)
        return PreparedStream(evidence=evidence, events=binder.bind_stream(tokens))

    async def _token_stream(
        self, question: str, evidence: Sequence[RetrievedChunk]
    ) -> AsyncIterator[str]:
        stream = getattr(self._generator, "generate_stream", None)
        if callable(stream):
            async for token in stream(question, evidence):
                yield token
            return
        # 模型适配器不支持流式时退化为整段输出；引用绑定仍逐句校验，
        # 但首字延迟由整段生成长度决定，部署时应优先使用流式适配器。
        yield await self._generator.generate(question, evidence)


async def _empty() -> AsyncIterator[str]:
    if False:  # pragma: no cover - 让函数成为异步生成器
        yield ""


__all__ = [
    "AnswerResult",
    "AnswerService",
    "EvidenceRetriever",
    "HybridRetrievalAdapter",
    "PreparedStream",
    "RetrievalServiceAdapter",
]

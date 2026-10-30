"""端到端答案编排：检索 → 模型 → 逐句引用绑定 → 答案/拒答。

检索与生成都是可注入的，便于替换为真实后端而不改编排逻辑。
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Protocol

from kb.generation.citations import (
    DEFAULT_REFUSAL,
    Answer,
    CitationBinder,
    Generator,
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
        evidence = await self._retriever.search(ctx, question)
        binder = CitationBinder(evidence, refusal_text=self._refusal_text)
        if not evidence:
            return binder.bind("")
        raw = await self._generator.generate(question, evidence)
        return binder.bind(raw)


__all__ = [
    "AnswerService",
    "EvidenceRetriever",
    "HybridRetrievalAdapter",
    "RetrievalServiceAdapter",
]

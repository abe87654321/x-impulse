"""生成编排：模型输出 → 逐句引用绑定 → 答案或拒答。"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence

from kb.generation.citations import (
    DEFAULT_REFUSAL,
    Answer,
    CitationBinder,
    Generator,
    StreamEvent,
)
from kb.retrieval.models import RetrievedChunk


class AnswerPipeline:
    def __init__(self, generator: Generator, *, refusal_text: str = DEFAULT_REFUSAL) -> None:
        self._generator = generator
        self._refusal_text = refusal_text

    async def answer(self, question: str, evidence: Sequence[RetrievedChunk]) -> Answer:
        binder = CitationBinder(evidence, refusal_text=self._refusal_text)
        if not evidence:
            return binder.bind("")
        raw = await self._generator.generate(question, evidence)
        return binder.bind(raw)

    async def answer_stream(
        self, tokens: AsyncIterator[str], *, evidence: Sequence[RetrievedChunk]
    ) -> AsyncIterator[StreamEvent]:
        binder = CitationBinder(evidence, refusal_text=self._refusal_text)
        async for event in binder.bind_stream(tokens):
            yield event


__all__ = ["AnswerPipeline"]

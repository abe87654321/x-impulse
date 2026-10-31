"""抽取式生成器 —— 无模型时的演示/兜底实现。

直接把证据原文作为句子并标注 [n]。它不会产生幻觉，但也谈不上"生成"；
仅用于链路演示与离线回归。生产使用 `OpenAICompatibleGenerator`。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence

from kb.retrieval.models import RetrievedChunk


class ExtractiveGenerator:
    def __init__(self, *, max_evidence: int = 3) -> None:
        if max_evidence <= 0:
            raise ValueError("max_evidence 必须 > 0")
        self._max_evidence = max_evidence

    async def generate(self, question: str, evidence: Sequence[RetrievedChunk]) -> str:
        parts: list[str] = []
        for index, item in enumerate(evidence[: self._max_evidence], start=1):
            text = item.record.content.strip()
            if not text:
                continue
            parts.append(f"{text}[{index}]")
        return "".join(parts)

    async def generate_stream(
        self, question: str, evidence: Sequence[RetrievedChunk]
    ) -> AsyncIterator[str]:
        """逐字输出，保持与 `generate` 相同的内容（FR-05-12）。"""
        raw = await self.generate(question, evidence)
        for char in raw:
            yield char


__all__ = ["ExtractiveGenerator"]

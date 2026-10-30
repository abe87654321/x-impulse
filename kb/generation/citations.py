"""生成与引用绑定 —— FR-05-02 / FR-05-05a / FR-05-20。

规则：

1. 模型必须在每个事实性句子后用 `[n]` 标注证据编号（n 为证据列表的 1-based 下标）。
2. 一句话若没有任何【有效】证据标记，整句删除（绑不上即不输出）。
3. 证据为空，或删除后一句不剩 → 拒答（FR-05-07）。
4. 每条 citation 记录证据快照：chunk_content_hash + chunk_version + doc_version。

本模块不调用 LLM，只做确定性的绑定与校验；模型由调用方注入（FR-05-15）。
FR-05-05b（语义蕴含）本期不做（N-5）。
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from kb.retrieval.models import ChunkRecord, RetrievedChunk

DEFAULT_REFUSAL = "知识库中未找到足够依据，无法回答该问题。"

_MARKER_RE = re.compile(r"\[\s*(\d+)\s*\]")
_SENTENCE_END = frozenset("。！？!?；;\n")


@dataclass(frozen=True)
class Citation:
    sentence_index: int
    chunk_id: str
    doc_id: str
    page_no: int | None
    section_path: str | None
    clause_no: str | None
    char_start: int | None
    char_end: int | None
    chunk_content_hash: str
    chunk_version: str
    doc_version: str


@dataclass(frozen=True)
class BoundSentence:
    sentence_index: int
    text: str
    citations: tuple[Citation, ...]


@dataclass(frozen=True)
class Answer:
    text: str
    sentences: tuple[BoundSentence, ...]
    citations: tuple[Citation, ...]
    refused: bool
    refusal_reason: str | None


@dataclass(frozen=True)
class StreamEvent:
    kind: Literal["sentence", "refusal"]
    text: str
    sentence_index: int | None
    citations: tuple[Citation, ...]


class Generator(Protocol):
    """模型适配层。输入带编号的证据，输出带 [n] 标记的答案。"""

    async def generate(self, question: str, evidence: Sequence[RetrievedChunk]) -> str: ...


def split_sentences(text: str) -> list[str]:
    """按中英文句末标点与换行切句；保留标点，不丢内容。"""
    sentences: list[str] = []
    start = 0
    for i, ch in enumerate(text):
        if ch in _SENTENCE_END:
            sentence = text[start : i + 1]
            if sentence.strip():
                sentences.append(sentence)
            start = i + 1
    tail = text[start:]
    if tail.strip():
        sentences.append(tail)
    return sentences


def build_prompt(question: str, evidence: Sequence[RetrievedChunk]) -> str:
    """给模型的最小约束提示。证据编号与 CitationBinder 的解析规则一致。"""
    blocks = []
    for index, item in enumerate(evidence, start=1):
        record = item.record
        blocks.append(f"[{index}] ({record.title}) {record.content}")
    joined = "\n\n".join(blocks)
    return (
        "只使用下列证据回答，不得引入外部知识。\n"
        "每个事实性句子末尾必须标注所依据的证据编号，例如 [1] 或 [2][3]。\n"
        "没有证据支撑的句子不要输出。\n\n"
        f"问题：{question}\n\n证据：\n{joined}"
    )


class CitationBinder:
    def __init__(
        self, evidence: Sequence[RetrievedChunk], *, refusal_text: str = DEFAULT_REFUSAL
    ) -> None:
        self._records: tuple[ChunkRecord, ...] = tuple(item.record for item in evidence)
        self._refusal_text = refusal_text

    def bind(self, raw_answer: str) -> Answer:
        if not self._records:
            return self._refusal("no_evidence")

        sentences: list[BoundSentence] = []
        for raw_sentence in split_sentences(raw_answer):
            bound = self._bind_sentence(raw_sentence, len(sentences))
            if bound is not None:
                sentences.append(bound)

        if not sentences:
            return self._refusal("no_grounded_sentence")

        return Answer(
            text="".join(s.text for s in sentences),
            sentences=tuple(sentences),
            citations=tuple(c for s in sentences for c in s.citations),
            refused=False,
            refusal_reason=None,
        )

    async def bind_stream(self, tokens: AsyncIterator[str]) -> AsyncIterator[StreamEvent]:
        """逐句校验，可与模型流式输出并行；不阻塞首字。"""
        if not self._records:
            yield StreamEvent(
                kind="refusal",
                text=self._refusal_text,
                sentence_index=None,
                citations=(),
            )
            return

        buffer = ""
        index = 0
        produced = 0
        async for token in tokens:
            buffer += token
            while True:
                end = _first_sentence_end(buffer)
                if end is None:
                    break
                raw_sentence, buffer = buffer[:end], buffer[end:]
                bound = self._bind_sentence(raw_sentence, index)
                if bound is not None:
                    produced += 1
                    yield StreamEvent(
                        kind="sentence",
                        text=bound.text,
                        sentence_index=bound.sentence_index,
                        citations=bound.citations,
                    )
                    index += 1

        if buffer.strip():
            bound = self._bind_sentence(buffer, index)
            if bound is not None:
                produced += 1
                yield StreamEvent(
                    kind="sentence",
                    text=bound.text,
                    sentence_index=bound.sentence_index,
                    citations=bound.citations,
                )

        if produced == 0:
            yield StreamEvent(
                kind="refusal",
                text=self._refusal_text,
                sentence_index=None,
                citations=(),
            )

    # ── 内部 ────────────────────────────────────────────

    def _bind_sentence(self, raw_sentence: str, sentence_index: int) -> BoundSentence | None:
        citations = self._citations_for(raw_sentence, sentence_index)
        if not citations:
            return None
        text = _MARKER_RE.sub("", raw_sentence).strip()
        if not text:
            return None
        return BoundSentence(sentence_index=sentence_index, text=text, citations=citations)

    def _citations_for(self, raw_sentence: str, sentence_index: int) -> tuple[Citation, ...]:
        seen: set[str] = set()
        citations: list[Citation] = []
        for match in _MARKER_RE.finditer(raw_sentence):
            position = int(match.group(1))
            if position < 1 or position > len(self._records):
                continue
            record = self._records[position - 1]
            if record.chunk_id in seen:
                continue
            seen.add(record.chunk_id)
            citations.append(_snapshot(record, sentence_index))
        return tuple(citations)

    def _refusal(self, reason: str) -> Answer:
        return Answer(
            text=self._refusal_text,
            sentences=(),
            citations=(),
            refused=True,
            refusal_reason=reason,
        )


def _snapshot(record: ChunkRecord, sentence_index: int) -> Citation:
    return Citation(
        sentence_index=sentence_index,
        chunk_id=record.chunk_id,
        doc_id=record.doc_id,
        page_no=record.page_no,
        section_path=record.section_path,
        clause_no=record.clause_no,
        char_start=record.char_start,
        char_end=record.char_end,
        chunk_content_hash=record.chunk_content_hash,
        chunk_version=str(record.chunk_version),
        doc_version=record.doc_version,
    )


def _first_sentence_end(buffer: str) -> int | None:
    for i, ch in enumerate(buffer):
        if ch in _SENTENCE_END:
            return i + 1
    return None


__all__ = [
    "Answer",
    "BoundSentence",
    "Citation",
    "CitationBinder",
    "DEFAULT_REFUSAL",
    "Generator",
    "StreamEvent",
    "build_prompt",
    "split_sentences",
]

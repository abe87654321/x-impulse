"""FR-05-05a：逐句校验可与流式输出并行，不阻塞首字。"""

from __future__ import annotations

from collections.abc import AsyncIterator

from kb.generation.citations import CitationBinder
from kb.retrieval.models import RetrievedChunk
from tests.retrieval.fakes import make_record


def _evidence(*chunk_ids: str) -> list[RetrievedChunk]:
    return [
        RetrievedChunk(record=make_record(cid, doc_id=f"doc_{cid}"), score=1.0) for cid in chunk_ids
    ]


async def _tokens(parts: list[str]) -> AsyncIterator[str]:
    for part in parts:
        yield part


async def test_yields_grounded_sentences_as_they_complete():
    binder = CitationBinder(_evidence("c1", "c2"))

    events = [
        e
        async for e in binder.bind_stream(
            _tokens(["报销需", "在三十日内提交[1]", "。无依据。其余[2]。"])
        )
    ]

    assert [e.kind for e in events] == ["sentence", "sentence"]
    assert [e.text for e in events] == ["报销需在三十日内提交。", "其余。"]
    assert [e.sentence_index for e in events] == [0, 1]


async def test_marker_arriving_in_later_token_stays_with_previous_sentence():
    binder = CitationBinder(_evidence("c1"))

    events = [e async for e in binder.bind_stream(_tokens(["事实句。", "[1]"]))]

    assert [e.kind for e in events] == ["sentence"]
    assert events[0].text == "事实句。"
    assert events[0].citations[0].chunk_id == "c1"


async def test_stream_without_grounded_sentence_ends_in_refusal():
    binder = CitationBinder(_evidence("c1"))

    events = [e async for e in binder.bind_stream(_tokens(["没有依据。", "也没有[9]。"]))]

    assert len(events) == 1
    assert events[0].kind == "refusal"
    assert events[0].text


async def test_stream_without_evidence_refuses_immediately():
    binder = CitationBinder([])
    seen: list[str] = []

    async def tokens() -> AsyncIterator[str]:
        seen.append("consumed")
        yield "任意[1]。"

    events = [e async for e in binder.bind_stream(tokens())]

    assert [e.kind for e in events] == ["refusal"]
    assert seen == []  # 没有证据时不应消费模型流

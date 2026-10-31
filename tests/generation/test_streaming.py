"""FR-05-05a：逐句校验可与流式输出并行，不阻塞首字。"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence

from kb.generation.answer_service import AnswerService
from kb.generation.citations import CitationBinder
from kb.generation.extractive import ExtractiveGenerator
from kb.retrieval.models import RetrievedChunk
from tests.retrieval.fakes import make_ctx, make_record


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


class _Retriever:
    def __init__(self, evidence: Sequence[RetrievedChunk]) -> None:
        self.evidence = evidence
        self.calls = 0

    async def search(self, ctx, query):  # noqa: ANN001, ANN201
        self.calls += 1
        return self.evidence


class _StreamingGenerator:
    def __init__(self, parts: list[str]) -> None:
        self.parts = parts
        self.consumed = False

    async def generate(self, question, evidence):  # noqa: ANN001, ANN201
        raise AssertionError("流式路径不应调用 generate")

    async def generate_stream(self, question, evidence):  # noqa: ANN001, ANN201
        for part in self.parts:
            self.consumed = True
            yield part


class _NonStreamingGenerator:
    async def generate(self, question, evidence):  # noqa: ANN001, ANN201
        return "事实句[1]。"


async def test_answer_service_streams_grounded_sentences():
    service = AnswerService(
        _Retriever(_evidence("c1")),
        _StreamingGenerator(["事实", "句[1]。"]),
    )

    prepared = await service.prepare_stream(make_ctx("alice"), "问题")
    events = [event async for event in prepared.events]

    assert [event.kind for event in events] == ["sentence"]
    assert events[0].text == "事实句。"
    assert events[0].citations[0].chunk_id == "c1"
    assert prepared.evidence[0].record.chunk_id == "c1"


async def test_answer_service_falls_back_to_buffered_generator():
    service = AnswerService(_Retriever(_evidence("c1")), _NonStreamingGenerator())

    prepared = await service.prepare_stream(make_ctx("alice"), "问题")
    events = [event async for event in prepared.events]

    assert [event.kind for event in events] == ["sentence"]
    assert events[0].text == "事实句。"


async def test_answer_service_stream_refuses_without_consuming_model():
    generator = _StreamingGenerator(["任意[1]。"])
    service = AnswerService(_Retriever([]), generator)

    prepared = await service.prepare_stream(make_ctx("alice"), "无命中")
    events = [event async for event in prepared.events]

    assert [event.kind for event in events] == ["refusal"]
    assert generator.consumed is False


async def test_extractive_generator_stream_matches_buffered_output():
    evidence = _evidence("c1", "c2")
    generator = ExtractiveGenerator()
    expected = await generator.generate("问题", evidence)

    streamed = [token async for token in generator.generate_stream("问题", evidence)]

    assert "".join(streamed) == expected

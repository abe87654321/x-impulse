"""端到端编排：检索 → 模型 → 引用绑定；空证据必须拒答且不调用模型。"""

from __future__ import annotations

from collections.abc import Sequence

from kb.generation.answer_service import AnswerService
from kb.identity.permissions import DelegationContext
from kb.retrieval.models import RetrievedChunk
from tests.retrieval.fakes import make_ctx, make_record


def _evidence(*chunk_ids: str) -> list[RetrievedChunk]:
    return [
        RetrievedChunk(record=make_record(cid, doc_id=f"doc_{cid}"), score=1.0) for cid in chunk_ids
    ]


class _Retriever:
    def __init__(self, evidence: Sequence[RetrievedChunk]) -> None:
        self.evidence = evidence
        self.calls = 0

    async def search(self, ctx: DelegationContext, query: str) -> Sequence[RetrievedChunk]:
        self.calls += 1
        return self.evidence


class _Generator:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls = 0
        self.question = ""

    async def generate(self, question: str, evidence: Sequence[RetrievedChunk]) -> str:
        self.calls += 1
        self.question = question
        return self.response


async def test_end_to_end_binds_citations():
    retriever = _Retriever(_evidence("c1"))
    generator = _Generator("报销需在三十日内提交[1]。")
    service = AnswerService(retriever, generator)

    answer = await service.answer(make_ctx("alice"), "报销时限？")

    assert answer.refused is False
    assert generator.calls == 1
    assert generator.question == "报销时限？"
    assert answer.citations[0].chunk_id == "c1"
    assert answer.text == "报销需在三十日内提交。"


async def test_no_evidence_refuses_without_calling_model():
    retriever = _Retriever([])
    generator = _Generator("不该被调用[1]。")
    service = AnswerService(retriever, generator)

    answer = await service.answer(make_ctx("alice"), "无命中问题")

    assert answer.refused is True
    assert answer.refusal_reason == "no_evidence"
    assert generator.calls == 0


async def test_unbindable_model_output_refuses():
    retriever = _Retriever(_evidence("c1"))
    generator = _Generator("没有任何标记的句子。")
    service = AnswerService(retriever, generator)

    answer = await service.answer(make_ctx("alice"), "问题")

    assert answer.refused is True
    assert answer.refusal_reason == "no_grounded_sentence"

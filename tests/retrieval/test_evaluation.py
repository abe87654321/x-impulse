"""FR-04-17：recall@10 / recall@50 基线与重排启用判定。"""

from __future__ import annotations

import json

import pytest

from kb.retrieval.evaluation import (
    EvaluationCase,
    RetrievalBaseline,
    decide_rerank,
    evaluate_baseline,
)
from kb.retrieval.models import RetrievedChunk
from tests.retrieval.fakes import make_ctx, make_record


class _FakeRetriever:
    def __init__(self, by_question: dict[str, list[str]]) -> None:
        self.by_question = by_question

    async def retrieve(self, ctx, query, *, k=None):
        chunk_ids = self.by_question[query]
        if k is not None:
            chunk_ids = chunk_ids[:k]
        return [RetrievedChunk(record=make_record(chunk_id), score=1.0) for chunk_id in chunk_ids]


def _cases() -> list[EvaluationCase]:
    return [
        EvaluationCase("case_1", "q1", frozenset({"c_easy"})),
        EvaluationCase("case_2", "q2", frozenset({"c_deep"})),
        EvaluationCase("case_3", "q3", frozenset({"c_missing"})),
    ]


def _retriever() -> _FakeRetriever:
    top_ten = [f"c_noise_{i}" for i in range(10)]
    return _FakeRetriever(
        {
            "q1": ["c_easy", *top_ten],
            "q2": [*top_ten, "c_deep"],
            "q3": [f"c_other_{i}" for i in range(50)],
        }
    )


async def test_measures_recall_at_both_depths():
    baseline = await evaluate_baseline(_retriever(), _cases(), make_ctx())

    assert baseline.case_count == 3
    assert baseline.hits_small == 1
    assert baseline.hits_large == 2
    assert baseline.recall_small == pytest.approx(1 / 3)
    assert baseline.recall_large == pytest.approx(2 / 3)
    assert baseline.gap == pytest.approx(1 / 3)


async def test_empty_eval_set_is_zero_not_division_error():
    baseline = await evaluate_baseline(_retriever(), [], make_ctx())

    assert baseline.case_count == 0
    assert baseline.recall_small == 0.0
    assert baseline.recall_large == 0.0


async def test_rejects_inverted_depths():
    with pytest.raises(ValueError):
        await evaluate_baseline(_retriever(), _cases(), make_ctx(), k_small=50, k_large=10)


def test_baseline_serializes_for_regression_reports():
    baseline = RetrievalBaseline(case_count=4, hits_small=2, hits_large=3)

    payload = json.loads(baseline.to_json())

    assert payload["recall_small"] == 0.5
    assert payload["recall_large"] == 0.75
    assert payload["gap"] == 0.25


def test_small_gap_never_enables_rerank():
    off = RetrievalBaseline(case_count=10, hits_small=5, hits_large=5)
    on = RetrievalBaseline(case_count=10, hits_small=9, hits_large=9)

    decision = decide_rerank(off, on, min_gap=0.05)

    assert decision.enabled is False
    assert decision.reason == "gap_below_threshold"


def test_missing_comparison_never_enables_rerank():
    off = RetrievalBaseline(case_count=10, hits_small=5, hits_large=9)

    decision = decide_rerank(off, None)

    assert decision.enabled is False
    assert decision.reason == "rerank_comparison_missing"


def test_no_measurable_gain_keeps_rerank_off():
    off = RetrievalBaseline(case_count=10, hits_small=5, hits_large=9)
    on = RetrievalBaseline(case_count=10, hits_small=5, hits_large=9)

    decision = decide_rerank(off, on)

    assert decision.enabled is False
    assert decision.reason == "no_measurable_gain"
    assert decision.gain == 0.0


def test_measured_gain_enables_rerank():
    off = RetrievalBaseline(case_count=10, hits_small=5, hits_large=9)
    on = RetrievalBaseline(case_count=10, hits_small=8, hits_large=9)

    decision = decide_rerank(off, on)

    assert decision.enabled is True
    assert decision.reason == "measured_gain"
    assert decision.gain == pytest.approx(0.3)
    assert decision.to_dict()["enabled"] is True

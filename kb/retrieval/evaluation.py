"""双深度召回基线 —— FR-04-17。

在客户自有评测集上同时测 recall@10 与 recall@50。二者差值是重排决策的
唯一依据：差值大说明"召回到了但排不上去"，重排可能有益；差值小说明
瓶颈在召回本身，重排无用甚至有害。

本模块也承担"开启与关闭重排两种配置的对照"（FR-04-08 验收）：
只有实测提升超过阈值时才允许启用重排；否则必须以关闭状态交付。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from kb.database import Database
from kb.identity.permissions import DelegationContext
from kb.retrieval.models import RetrievedChunk


class RetrieverLike(Protocol):
    async def retrieve(
        self, ctx: DelegationContext, query: str, *, k: int | None = None
    ) -> Sequence[RetrievedChunk]: ...


@dataclass(frozen=True)
class EvaluationCase:
    case_id: str
    question: str
    expected_chunk_ids: frozenset[str]


@dataclass(frozen=True)
class RetrievalBaseline:
    case_count: int
    hits_small: int
    hits_large: int
    k_small: int = 10
    k_large: int = 50

    @property
    def recall_small(self) -> float:
        return self.hits_small / self.case_count if self.case_count else 0.0

    @property
    def recall_large(self) -> float:
        return self.hits_large / self.case_count if self.case_count else 0.0

    @property
    def gap(self) -> float:
        return self.recall_large - self.recall_small

    def to_dict(self) -> dict[str, object]:
        return {
            "case_count": self.case_count,
            "k_small": self.k_small,
            "k_large": self.k_large,
            "hits_small": self.hits_small,
            "hits_large": self.hits_large,
            "recall_small": round(self.recall_small, 6),
            "recall_large": round(self.recall_large, 6),
            "gap": round(self.gap, 6),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)


@dataclass(frozen=True)
class RerankDecision:
    enabled: bool
    reason: str
    gap: float
    gain: float | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "reason": self.reason,
            "gap": round(self.gap, 6),
            "gain": None if self.gain is None else round(self.gain, 6),
        }


async def evaluate_baseline(
    retriever: RetrieverLike,
    cases: Sequence[EvaluationCase],
    ctx: DelegationContext,
    *,
    k_small: int = 10,
    k_large: int = 50,
) -> RetrievalBaseline:
    if k_small <= 0 or k_large <= 0:
        raise ValueError("k 必须 > 0")
    if k_small > k_large:
        raise ValueError("k_small 不得大于 k_large")
    if not cases:
        return RetrievalBaseline(
            case_count=0, hits_small=0, hits_large=0, k_small=k_small, k_large=k_large
        )

    hits_small = 0
    hits_large = 0
    for case in cases:
        results = await retriever.retrieve(ctx, case.question, k=k_large)
        chunk_ids = [item.record.chunk_id for item in results]
        if case.expected_chunk_ids.intersection(chunk_ids[:k_small]):
            hits_small += 1
        if case.expected_chunk_ids.intersection(chunk_ids[:k_large]):
            hits_large += 1
    return RetrievalBaseline(
        case_count=len(cases),
        hits_small=hits_small,
        hits_large=hits_large,
        k_small=k_small,
        k_large=k_large,
    )


def decide_rerank(
    baseline_off: RetrievalBaseline,
    baseline_on: RetrievalBaseline | None,
    *,
    min_gap: float = 0.05,
    min_gain: float = 0.0,
) -> RerankDecision:
    """FR-04-08 验收：差值过小不启用；有差值但实测无提升也不启用。"""
    gap = baseline_off.gap
    if gap < min_gap:
        return RerankDecision(enabled=False, reason="gap_below_threshold", gap=gap)
    if baseline_on is None:
        return RerankDecision(enabled=False, reason="rerank_comparison_missing", gap=gap)
    gain = baseline_on.recall_small - baseline_off.recall_small
    if gain <= min_gain:
        return RerankDecision(enabled=False, reason="no_measurable_gain", gap=gap, gain=gain)
    return RerankDecision(enabled=True, reason="measured_gain", gap=gap, gain=gain)


def load_eval_cases(db: Database, *, tenant_id: str, set_id: str) -> list[EvaluationCase]:
    rows = db.fetch(
        "SELECT case_id, question, expected_chunk_ids FROM eval_cases "
        "WHERE tenant_id = $1 AND set_id = $2 ORDER BY case_id",
        tenant_id,
        set_id,
    )
    return [
        EvaluationCase(
            case_id=str(row["case_id"]),
            question=str(row["question"]),
            expected_chunk_ids=frozenset(str(cid) for cid in row["expected_chunk_ids"]),
        )
        for row in rows
    ]


__all__ = [
    "EvaluationCase",
    "RerankDecision",
    "RetrievalBaseline",
    "RetrieverLike",
    "decide_rerank",
    "evaluate_baseline",
    "load_eval_cases",
]

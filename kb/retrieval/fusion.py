"""两路召回融合 —— FR-04-05。

默认 weighted 且偏向 BM25；实际权重必须在客户评测集上标定后写入交付方案。
"""

from __future__ import annotations

from collections.abc import Sequence

from kb.retrieval.models import (
    HybridRecall,
    RetrievalConfig,
    ScoredChunk,
)


def fuse(recall: HybridRecall, config: RetrievalConfig) -> list[ScoredChunk]:
    if config.fusion == "rrf":
        return rrf_fuse(
            recall.bm25,
            recall.vector,
            bm25_weight=config.bm25_weight,
            vector_weight=config.vector_weight,
            rrf_k=config.rrf_k,
        )
    return weighted_fuse(
        recall.bm25,
        recall.vector,
        bm25_weight=config.bm25_weight,
        vector_weight=config.vector_weight,
    )


def weighted_fuse(
    bm25: Sequence[ScoredChunk],
    vector: Sequence[ScoredChunk],
    *,
    bm25_weight: float,
    vector_weight: float,
) -> list[ScoredChunk]:
    """按腿内最大值归一化后加权求和。同一 chunk 在两路出现时合并。"""
    merged: dict[str, tuple[ScoredChunk, float]] = {}
    for leg, weight in ((bm25, bm25_weight), (vector, vector_weight)):
        for scored, norm in _normalize(leg):
            cid = scored.record.chunk_id
            contribution = weight * norm
            existing = merged.get(cid)
            if existing is None:
                merged[cid] = (scored, contribution)
            else:
                merged[cid] = (existing[0], existing[1] + contribution)

    return _sorted_scores(merged)


def rrf_fuse(
    bm25: Sequence[ScoredChunk],
    vector: Sequence[ScoredChunk],
    *,
    bm25_weight: float,
    vector_weight: float,
    rrf_k: int,
) -> list[ScoredChunk]:
    """Reciprocal Rank Fusion；权重作用在每一腿的贡献上。"""
    merged: dict[str, tuple[ScoredChunk, float]] = {}
    for leg, weight in ((bm25, bm25_weight), (vector, vector_weight)):
        for rank, scored in enumerate(leg, start=1):
            contribution = weight / (rrf_k + rank)
            cid = scored.record.chunk_id
            existing = merged.get(cid)
            if existing is None:
                merged[cid] = (scored, contribution)
            else:
                merged[cid] = (existing[0], existing[1] + contribution)
    return _sorted_scores(merged)


def _normalize(leg: Sequence[ScoredChunk]) -> list[tuple[ScoredChunk, float]]:
    if not leg:
        return []
    top = max(s.score for s in leg)
    if top <= 0:
        return [(s, 0.0) for s in leg]
    return [(s, s.score / top) for s in leg]


def _sorted_scores(
    merged: dict[str, tuple[ScoredChunk, float]],
) -> list[ScoredChunk]:
    ordered = sorted(merged.values(), key=lambda item: item[1], reverse=True)
    return [ScoredChunk(record=scored.record, score=score) for scored, score in ordered]

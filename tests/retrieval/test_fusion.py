"""FR-04-05 融合测试：默认偏向 BM25，权重可配，RRF 去重。"""

from __future__ import annotations

from kb.retrieval.fusion import fuse
from kb.retrieval.models import HybridRecall, RetrievalConfig, ScoredChunk
from tests.retrieval.fakes import make_record


def _cfg(**kwargs) -> RetrievalConfig:
    return RetrievalConfig(audience_allowlist=frozenset({"internal_only"}), **kwargs)


def test_default_weights_favor_bm25_over_vector():
    a = ScoredChunk(make_record("a"), 1.0)
    b = ScoredChunk(make_record("b"), 1.0)
    result = fuse(HybridRecall(bm25=(a,), vector=(b,)), _cfg())
    assert [s.record.chunk_id for s in result] == ["a", "b"]
    assert result[0].score > result[1].score


def test_weights_are_configurable():
    a = ScoredChunk(make_record("a"), 1.0)
    b = ScoredChunk(make_record("b"), 1.0)
    result = fuse(
        HybridRecall(bm25=(a,), vector=(b,)),
        _cfg(bm25_weight=0.0, vector_weight=1.0),
    )
    assert [s.record.chunk_id for s in result] == ["b", "a"]


def test_rrf_merges_same_chunk_from_both_legs():
    a = ScoredChunk(make_record("a"), 1.0)
    b = ScoredChunk(make_record("b"), 1.0)
    result = fuse(
        HybridRecall(bm25=(a, b), vector=(a,)),
        _cfg(fusion="rrf"),
    )
    assert [s.record.chunk_id for s in result] == ["a", "b"]
    assert result[0].score > result[1].score

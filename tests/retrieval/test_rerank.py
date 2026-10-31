"""FR-04-08：重排是显式开关，且只看到已通过权限校验的候选。"""

from __future__ import annotations

import sys
import types

import pytest

from kb.retrieval.models import RetrievalConfig, ScoredChunk
from kb.retrieval.rerank import (
    CrossEncoderReranker,
    RerankConfigError,
    build_reranker_from_env,
)
from kb.retrieval.retriever import Retriever
from tests.retrieval.fakes import (
    FakeBackend,
    FakeRepository,
    MapPermissions,
    make_ctx,
    make_record,
)


class _FakeReranker:
    model_version = "fake-v1"

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def rerank(self, query, candidates, *, limit):
        self.calls.append([candidate.record.chunk_id for candidate in candidates])
        ranked = sorted(candidates, key=lambda item: item.record.chunk_id, reverse=True)
        return ranked[:limit]


def _config(**kwargs) -> RetrievalConfig:
    return RetrievalConfig(audience_allowlist=frozenset({"internal_only"}), **kwargs)


def _retriever(records, permissions, *, config, reranker=None) -> Retriever:
    return Retriever(
        backend=FakeBackend(records),
        repository=FakeRepository(records),
        permissions=permissions,
        config=config,
        reranker=reranker,
    )


async def test_rerank_reorders_after_permission_revalidation():
    records = [
        make_record("c_a", doc_id="doc_a"),
        make_record("c_b", doc_id="doc_b"),
        make_record("c_c", doc_id="doc_c"),
    ]
    perms = MapPermissions(
        {"alice": frozenset({"doc_a", "doc_b", "doc_c"})}, denied_docs=frozenset({"doc_b"})
    )
    reranker = _FakeReranker()
    r = _retriever(
        records,
        perms,
        config=_config(top_k=2, rerank_enabled=True, rerank_top_k=3),
        reranker=reranker,
    )

    result = await r.retrieve(make_ctx(), "q", tenant_id="t1")

    assert reranker.calls == [["c_a", "c_c"]]
    assert [item.record.chunk_id for item in result] == ["c_c", "c_a"]


async def test_disabled_rerank_is_not_called():
    records = [make_record("c_a", doc_id="doc_a")]
    reranker = _FakeReranker()
    r = _retriever(
        records,
        MapPermissions({"alice": frozenset({"doc_a"})}),
        config=_config(top_k=1, rerank_enabled=False),
        reranker=reranker,
    )

    result = await r.retrieve(make_ctx(), "q", tenant_id="t1")

    assert reranker.calls == []
    assert [item.record.chunk_id for item in result] == ["c_a"]


async def test_enabled_without_reranker_fails_closed():
    with pytest.raises(ValueError):
        _retriever(
            [],
            MapPermissions({}),
            config=_config(rerank_enabled=True),
        )


class _FakeCrossEncoder:
    def __init__(self, model: str, device: str | None = None) -> None:
        self.model = model
        self.device = device
        self.pairs: list[tuple[str, str]] = []

    def predict(self, pairs):
        self.pairs = list(pairs)
        return [0.1 * index for index, _ in enumerate(pairs)]


def _install_fake_cross_encoder(monkeypatch) -> None:
    module = types.SimpleNamespace(CrossEncoder=_FakeCrossEncoder)
    monkeypatch.setitem(sys.modules, "sentence_transformers", module)


async def test_cross_encoder_scores_and_truncates(monkeypatch):
    _install_fake_cross_encoder(monkeypatch)
    reranker = CrossEncoderReranker("bge-reranker-base", device="cpu")
    candidates = [
        ScoredChunk(record=make_record("c_1"), score=0.9),
        ScoredChunk(record=make_record("c_2"), score=0.8),
    ]

    ranked = await reranker.rerank("问题", candidates, limit=1)

    assert [item.record.chunk_id for item in ranked] == ["c_2"]
    assert reranker._model.pairs == [("问题", "content of c_1"), ("问题", "content of c_2")]


def test_factory_returns_none_when_not_enabled():
    assert build_reranker_from_env({}) is None
    assert build_reranker_from_env({"KB_RERANK_ENABLED": "false"}) is None


def test_factory_requires_model_when_enabled():
    with pytest.raises(RerankConfigError):
        build_reranker_from_env({"KB_RERANK_ENABLED": "true"})


def test_factory_rejects_unknown_provider():
    with pytest.raises(RerankConfigError):
        build_reranker_from_env({"KB_RERANK_ENABLED": "true", "KB_RERANKER": "magic"})


def test_factory_builds_cross_encoder(monkeypatch):
    _install_fake_cross_encoder(monkeypatch)
    reranker = build_reranker_from_env(
        {"KB_RERANK_ENABLED": "true", "KB_RERANK_MODEL": "bge-reranker-base"}
    )

    assert isinstance(reranker, CrossEncoderReranker)
    assert reranker.model_version == "bge-reranker-base"

"""嵌入后端工厂 —— 显式选择，缺失即报错，禁止静默降级。"""

from __future__ import annotations

import sys
import types

import pytest

from kb.indexing.embedding import (
    EmbeddingConfigError,
    HashingEmbedder,
    SentenceTransformerEmbedder,
    build_embedder_from_env,
)


class _FakeModel:
    def __init__(self, model: str, device: str | None = None) -> None:
        self.model = model
        self.device = device
        self.calls: list[tuple[list[str], int, bool]] = []

    def get_sentence_embedding_dimension(self) -> int:
        return 3

    def encode(self, texts, *, batch_size=32, normalize_embeddings=False):
        self.calls.append((list(texts), batch_size, normalize_embeddings))
        return [[0.1, 0.2, 0.3] for _ in texts]


def _install_fake(monkeypatch, model_cls=_FakeModel) -> None:
    module = types.SimpleNamespace(SentenceTransformer=model_cls)
    monkeypatch.setitem(sys.modules, "sentence_transformers", module)


async def test_sentence_transformer_embedder_encodes_normalized(monkeypatch):
    _install_fake(monkeypatch)
    embedder = SentenceTransformerEmbedder("bge-m3", device="cpu", batch_size=8)

    vectors = await embedder.embed(["甲", "乙"])

    assert embedder.dimension == 3
    assert embedder.model_version == "bge-m3"
    assert vectors == [[0.1, 0.2, 0.3], [0.1, 0.2, 0.3]]
    assert embedder._model.calls == [(["甲", "乙"], 8, True)]


async def test_empty_input_skips_model_call(monkeypatch):
    _install_fake(monkeypatch)
    embedder = SentenceTransformerEmbedder("bge-m3")

    assert await embedder.embed([]) == []
    assert embedder._model.calls == []


def test_missing_ml_dependency_fails_closed(monkeypatch):
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)

    with pytest.raises(EmbeddingConfigError):
        SentenceTransformerEmbedder("bge-m3")


def test_factory_requires_explicit_provider():
    with pytest.raises(EmbeddingConfigError):
        build_embedder_from_env({})


def test_factory_builds_hashing_for_dev():
    embedder = build_embedder_from_env({"KB_EMBEDDER": "hashing", "KB_EMBEDDING_DIMENSION": "16"})

    assert isinstance(embedder, HashingEmbedder)
    assert embedder.dimension == 16


def test_factory_sentence_transformer_requires_model():
    with pytest.raises(EmbeddingConfigError):
        build_embedder_from_env({"KB_EMBEDDER": "sentence-transformers"})


def test_factory_rejects_unknown_provider():
    with pytest.raises(EmbeddingConfigError):
        build_embedder_from_env({"KB_EMBEDDER": "magic"})

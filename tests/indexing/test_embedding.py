"""HashingEmbedder 的确定性与形状。"""

from __future__ import annotations

from kb.indexing.embedding import HashingEmbedder


async def test_embedding_is_deterministic_and_normalized():
    embedder = HashingEmbedder(dimension=32)

    first = await embedder.embed(["报销制度", "another text"])
    second = await embedder.embed(["报销制度", "another text"])

    assert first == second
    assert len(first) == 2
    assert all(len(vector) == 32 for vector in first)
    norm = sum(value * value for value in first[0]) ** 0.5
    assert abs(norm - 1.0) < 1e-9


async def test_different_texts_produce_different_vectors():
    embedder = HashingEmbedder(dimension=32)
    a, b = await embedder.embed(["报销标准", "员工手册"])

    assert a != b

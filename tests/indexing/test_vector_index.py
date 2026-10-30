"""索引流水线 → 向量库 → chunk_vectors 映射。"""

from __future__ import annotations

from kb.indexing.embedding import HashingEmbedder
from kb.indexing.pipeline import IndexingPipeline
from kb.indexing.vector_index import VectorIndexer
from kb.indexing.vector_store import InMemoryVectorStore
from kb.parsing.chunking import ChunkingConfig
from tests.indexing.fakes import FakeConnector, FakeDoc, InMemoryStore

TENANT = "default"
SOURCE = "src_vec"


async def test_pipeline_writes_vectors_and_mapping():
    store = InMemoryStore()
    vectors = InMemoryVectorStore()
    indexer = VectorIndexer(
        HashingEmbedder(dimension=16),
        vectors,
        collection="test_chunks",
        vector_store_name="memory",
    )
    pipeline = IndexingPipeline(
        FakeConnector([FakeDoc("ext-1", "报销需在三十日内提交。附发票。")]),
        store,
        tenant_id=TENANT,
        source_id=SOURCE,
        chunking=ChunkingConfig(target_tokens=10, max_tokens=20, min_tokens=3, overlap_ratio=0.0),
        vector_indexer=indexer,
    )

    stats = await pipeline.run()

    assert stats.indexed == 1
    prepared = next(iter(store.prepared.values()))
    chunk_ids = {chunk.chunk_id for chunk in prepared.chunks}
    assert set(store.chunk_vectors) == chunk_ids
    entry = next(iter(store.chunk_vectors.values()))
    assert entry.model_name == "hashing"
    assert entry.collection == "test_chunks"
    assert entry.vector_store == "memory"

    hits = await vectors.search(
        "test_chunks",
        (0.0,) * 16,
        tenant_id=TENANT,
        allowed_doc_ids=frozenset({prepared.doc_id}),
        audience_allowlist=frozenset({"internal_only"}),
        limit=10,
    )
    assert {hit.chunk_id for hit in hits} == chunk_ids

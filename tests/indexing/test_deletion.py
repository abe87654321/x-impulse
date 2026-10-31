"""源端删除传播：DB、向量库、缓存三处同时收敛。"""

from __future__ import annotations

from kb.indexing.deletion import DocumentDeletionService
from kb.indexing.embedding import HashingEmbedder
from kb.indexing.pipeline import IndexingPipeline
from kb.indexing.raw_store import InMemoryRawObjectStore
from kb.indexing.vector_index import VectorIndexer
from kb.indexing.vector_store import InMemoryVectorStore
from kb.parsing.chunking import ChunkingConfig
from kb.retrieval.cache import InMemoryQueryCache
from tests.indexing.fakes import FakeConnector, FakeDoc, InMemoryStore

TENANT = "default"
SOURCE = "src_delete"
COLLECTION = "delete_it"


async def test_delete_removes_db_vectors_and_cache():
    store = InMemoryStore()
    vectors = InMemoryVectorStore()
    embedder = HashingEmbedder(dimension=16)
    indexer = VectorIndexer(embedder, vectors, collection=COLLECTION, vector_store_name="memory")
    pipeline = IndexingPipeline(
        FakeConnector([FakeDoc("ext-1", "报销需在三十日内提交。附发票。")]),
        store,
        tenant_id=TENANT,
        source_id=SOURCE,
        chunking=ChunkingConfig(target_tokens=10, max_tokens=20, min_tokens=3, overlap_ratio=0.0),
        vector_indexer=indexer,
    )
    await pipeline.run()
    prepared = next(iter(store.prepared.values()))
    chunk_ids = [chunk.chunk_id for chunk in prepared.chunks]

    cache = InMemoryQueryCache()
    await cache.put(TENANT, "q", chunk_ids)

    service = DocumentDeletionService(store, vectors, cache, collection=COLLECTION)
    result = await service.delete_document(TENANT, prepared.doc_id)

    assert set(result.chunk_ids) == set(chunk_ids)
    assert store.prepared == {}
    assert store.documents == {}
    assert store.chunk_vectors == {}
    assert await cache.get(TENANT, "q") is None
    hits = await vectors.search(
        COLLECTION,
        (0.0,) * 16,
        tenant_id=TENANT,
        allowed_doc_ids=frozenset({prepared.doc_id}),
        audience_allowlist=frozenset({"internal_only"}),
        limit=10,
    )
    assert hits == []


class _SubjectCleaner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str]]] = []

    async def delete_chunk_locations(self, tenant_id, chunk_ids):
        self.calls.append((tenant_id, list(chunk_ids)))


async def test_delete_removes_raw_objects_and_subject_index_rows():
    store = InMemoryStore()
    vectors = InMemoryVectorStore()
    indexer = VectorIndexer(
        HashingEmbedder(dimension=16),
        vectors,
        collection=COLLECTION,
        vector_store_name="memory",
    )
    pipeline = IndexingPipeline(
        FakeConnector([FakeDoc("ext-raw", "原文与索引都要清。")]),
        store,
        tenant_id=TENANT,
        source_id=SOURCE,
        chunking=ChunkingConfig(target_tokens=10, max_tokens=20, min_tokens=3, overlap_ratio=0.0),
        vector_indexer=indexer,
    )
    await pipeline.run()
    prepared = next(iter(store.prepared.values()))
    chunk_ids = [chunk.chunk_id for chunk in prepared.chunks]
    raw_store = InMemoryRawObjectStore()
    await raw_store.put(TENANT, prepared.doc_id, "h1", b"raw")
    subject_cleaner = _SubjectCleaner()

    service = DocumentDeletionService(
        store,
        vectors,
        InMemoryQueryCache(),
        collection=COLLECTION,
        raw_store=raw_store,
        subject_indexer=subject_cleaner,
    )
    result = await service.delete_document(TENANT, prepared.doc_id)

    assert result.raw_objects_deleted == 1
    assert result.subject_locations_deleted == len(chunk_ids)
    assert await raw_store.get(TENANT, prepared.doc_id, "h1") is None
    assert subject_cleaner.calls == [(TENANT, chunk_ids)]

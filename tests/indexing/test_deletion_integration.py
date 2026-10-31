"""PostgreSQL 删除传播集成测试。"""

from __future__ import annotations

import asyncio

import pytest

from kb.database import Database
from kb.indexing.deletion import DocumentDeletionService
from kb.indexing.embedding import HashingEmbedder
from kb.indexing.ids import document_id
from kb.indexing.pipeline import IndexingPipeline
from kb.indexing.store import PostgresDocumentStore
from kb.indexing.vector_index import VectorIndexer
from kb.indexing.vector_store import InMemoryVectorStore
from kb.parsing.chunking import ChunkingConfig
from kb.retrieval.cache import InMemoryQueryCache
from tests.indexing.fakes import FakeConnector, FakeDoc

pytestmark = pytest.mark.integration

TENANT = "default"
SOURCE = "src_delete_it"
EXTERNAL = "ext-del-1"
COLLECTION = "delete_it"


def _cleanup() -> None:
    db = Database()
    db.execute("DELETE FROM acl_entries WHERE source_id = $1", SOURCE)
    db.execute(
        """
        DELETE FROM chunk_vectors cv USING chunks c
        WHERE c.chunk_id = cv.chunk_id AND c.doc_id = $1
        """,
        document_id(SOURCE, EXTERNAL),
    )
    db.execute("DELETE FROM chunks WHERE doc_id = $1", document_id(SOURCE, EXTERNAL))
    db.execute("DELETE FROM annotations WHERE doc_id = $1", document_id(SOURCE, EXTERNAL))
    db.execute("DELETE FROM documents WHERE source_id = $1", SOURCE)
    db.execute("DELETE FROM sources WHERE source_id = $1", SOURCE)


def test_deletion_propagates_to_db_vectors_and_cache():
    _cleanup()
    Database().execute(
        """
        INSERT INTO sources (source_id, tenant_id, source_type, display_name)
        VALUES ($1, $2, 'upload', '删除传播集成测试')
        ON CONFLICT (source_id) DO NOTHING
        """,
        SOURCE,
        TENANT,
    )
    try:
        vectors = InMemoryVectorStore()
        embedder = HashingEmbedder(dimension=16)
        store = PostgresDocumentStore()
        pipeline = IndexingPipeline(
            FakeConnector([FakeDoc(EXTERNAL, "报销需在三十日内提交。附发票。")]),
            store,
            tenant_id=TENANT,
            source_id=SOURCE,
            chunking=ChunkingConfig(
                target_tokens=10, max_tokens=20, min_tokens=3, overlap_ratio=0.0
            ),
            vector_indexer=VectorIndexer(
                embedder, vectors, collection=COLLECTION, vector_store_name="memory"
            ),
        )
        asyncio.run(pipeline.run())

        doc_id = document_id(SOURCE, EXTERNAL)
        chunk_ids = asyncio.run(store.list_chunk_ids(TENANT, doc_id))
        assert chunk_ids

        cache = InMemoryQueryCache()
        asyncio.run(cache.put(TENANT, "q", chunk_ids))
        service = DocumentDeletionService(store, vectors, cache, collection=COLLECTION)
        result = asyncio.run(service.delete_document(TENANT, doc_id))

        assert set(result.chunk_ids) == set(chunk_ids)
        db = Database()
        assert db.fetch("SELECT 1 FROM documents WHERE doc_id = $1", doc_id) == []
        assert db.fetch("SELECT 1 FROM chunks WHERE doc_id = $1", doc_id) == []
        assert db.fetch("SELECT 1 FROM acl_entries WHERE object_id = $1", doc_id) == []
        assert asyncio.run(cache.get(TENANT, "q")) is None
    finally:
        _cleanup()

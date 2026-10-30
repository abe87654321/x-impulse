"""删除后再次同步：抑制清单必须挡住重新入库（设计 §7）。"""

from __future__ import annotations

import asyncio

import pytest

from kb.compliance.erasure import DocumentErasureService, PostgresErasureRepository
from kb.compliance.pii import subject_key
from kb.database import Database
from kb.indexing.embedding import HashingEmbedder
from kb.indexing.ids import document_id
from kb.indexing.pipeline import IndexingPipeline
from kb.indexing.store import PostgresDocumentStore
from kb.indexing.suppression import PostgresSuppressionChecker, document_scope_key
from kb.indexing.vector_index import VectorIndexer
from kb.indexing.vector_store import InMemoryVectorStore
from kb.parsing.chunking import ChunkingConfig
from kb.retrieval.cache import InMemoryQueryCache
from tests.indexing.fakes import FakeConnector, FakeDoc

pytestmark = pytest.mark.integration

TENANT = "default"
SOURCE = "src_suppress_it"
EXTERNAL = "ext-suppress-1"
DOC = document_id(SOURCE, EXTERNAL)
PEPPER = "suppress-pepper"
COLLECTION = "suppress_it"


def _hasher(value: str) -> str:
    return subject_key(value, PEPPER)


def _cleanup() -> None:
    db = Database()
    db.execute(
        "DELETE FROM suppression_list WHERE tenant_id = $1 "
        "AND scope_type = 'document' AND scope_key = $2",
        TENANT,
        _hasher(document_scope_key(SOURCE, EXTERNAL)),
    )
    db.execute("DELETE FROM erasure_certificates WHERE request_id = $1", "era_x")
    db.execute("DELETE FROM erasure_certificates WHERE tenant_id = $1", TENANT)
    db.execute("DELETE FROM erasure_scopes WHERE tenant_id = $1", TENANT)
    db.execute("DELETE FROM erasure_requests WHERE tenant_id = $1", TENANT)
    db.execute("DELETE FROM acl_entries WHERE source_id = $1", SOURCE)
    db.execute(
        "DELETE FROM chunk_vectors cv USING chunks c "
        "WHERE c.chunk_id = cv.chunk_id AND c.doc_id = $1",
        DOC,
    )
    db.execute("DELETE FROM chunks WHERE doc_id = $1", DOC)
    db.execute("DELETE FROM annotations WHERE doc_id = $1", DOC)
    db.execute("DELETE FROM documents WHERE source_id = $1", SOURCE)
    db.execute("DELETE FROM sources WHERE source_id = $1", SOURCE)


def _pipeline(connector: FakeConnector, *, suppression=None) -> IndexingPipeline:
    return IndexingPipeline(
        connector,
        PostgresDocumentStore(),
        tenant_id=TENANT,
        source_id=SOURCE,
        chunking=ChunkingConfig(target_tokens=10, max_tokens=20, min_tokens=3, overlap_ratio=0.0),
        suppression=suppression,
        vector_indexer=VectorIndexer(
            HashingEmbedder(dimension=16),
            InMemoryVectorStore(),
            collection=COLLECTION,
            vector_store_name="memory",
        ),
    )


def test_deleted_document_is_not_reindexed_by_next_sync():
    _cleanup()
    Database().execute(
        """
        INSERT INTO sources (source_id, tenant_id, source_type, display_name)
        VALUES ($1,$2,'upload','抑制清单集成测试')
        ON CONFLICT (source_id) DO NOTHING
        """,
        SOURCE,
        TENANT,
    )
    try:
        connector = FakeConnector([FakeDoc(EXTERNAL, "这段内容删除后不应回来。")])
        asyncio.run(_pipeline(connector).run())
        assert Database().fetch("SELECT 1 FROM documents WHERE doc_id = $1", DOC)

        eraser = DocumentErasureService(
            PostgresErasureRepository(),
            PostgresDocumentStore(),
            InMemoryVectorStore(),
            InMemoryQueryCache(),
            scope_key_hasher=_hasher,
            collection=COLLECTION,
        )
        asyncio.run(eraser.erase_document(TENANT, DOC))
        assert Database().fetch("SELECT 1 FROM documents WHERE doc_id = $1", DOC) == []

        checker = PostgresSuppressionChecker(_hasher)
        stats = asyncio.run(_pipeline(connector, suppression=checker).run())

        assert stats.indexed == 0
        assert stats.suppressed == 1
        assert Database().fetch("SELECT 1 FROM documents WHERE doc_id = $1", DOC) == []
    finally:
        _cleanup()

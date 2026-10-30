"""删除 chunk 前必须把 citations.chunk_id 置空，保留证据快照（R-11）。"""

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
SOURCE = "src_cit_it"
EXTERNAL = "ext-cit-1"
COLLECTION = "cit_it"
SESSION = "sess_cit_it"
QUERY = "qry_cit_it"
ANSWER = "ans_cit_it"
CITATION = "cit_it"


def _cleanup() -> None:
    db = Database()
    db.execute("DELETE FROM citations WHERE answer_id = $1", ANSWER)
    db.execute("DELETE FROM answers WHERE answer_id = $1", ANSWER)
    db.execute("DELETE FROM queries WHERE query_id = $1", QUERY)
    db.execute("DELETE FROM sessions WHERE session_id = $1", SESSION)
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


def test_citation_chunk_id_is_detached_and_snapshot_kept():
    _cleanup()
    Database().execute(
        """
        INSERT INTO sources (source_id, tenant_id, source_type, display_name)
        VALUES ($1, $2, 'upload', '引用悬空集成测试')
        ON CONFLICT (source_id) DO NOTHING
        """,
        SOURCE,
        TENANT,
    )
    try:
        vectors = InMemoryVectorStore()
        store = PostgresDocumentStore()
        pipeline = IndexingPipeline(
            FakeConnector([FakeDoc(EXTERNAL, "报销需在三十日内提交。")]),
            store,
            tenant_id=TENANT,
            source_id=SOURCE,
            chunking=ChunkingConfig(
                target_tokens=10, max_tokens=20, min_tokens=3, overlap_ratio=0.0
            ),
            vector_indexer=VectorIndexer(
                HashingEmbedder(dimension=16),
                vectors,
                collection=COLLECTION,
                vector_store_name="memory",
            ),
        )
        asyncio.run(pipeline.run())
        doc_id = document_id(SOURCE, EXTERNAL)
        chunk_ids = asyncio.run(store.list_chunk_ids(TENANT, doc_id))
        chunk_id = chunk_ids[0]

        db = Database()
        db.execute(
            """
            INSERT INTO sessions (session_id, tenant_id, principal_id, channel)
            VALUES ($1, $2, 'alice', 'web')
            """,
            SESSION,
            TENANT,
        )
        db.execute(
            """
            INSERT INTO queries
                (query_id, tenant_id, session_id, principal_id, channel, text_hash, text_length)
            VALUES ($1, $2, $3, 'alice', 'web', 'hash', 4)
            """,
            QUERY,
            TENANT,
            SESSION,
        )
        db.execute(
            """
            INSERT INTO answers
                (answer_id, tenant_id, query_id, text, model_name, model_version, latency_ms)
            VALUES ($1, $2, $3, '答案[1]。', 'test', 'v1', 10)
            """,
            ANSWER,
            TENANT,
            QUERY,
        )
        db.execute(
            """
            INSERT INTO citations
                (citation_id, tenant_id, answer_id, sentence_index, chunk_id, doc_id,
                 chunk_content_hash, chunk_version, doc_version)
            VALUES ($1, $2, $3, 0, $4, $5, 'snapshot-hash', '1', '1')
            """,
            CITATION,
            TENANT,
            ANSWER,
            chunk_id,
            doc_id,
        )

        service = DocumentDeletionService(
            store, vectors, InMemoryQueryCache(), collection=COLLECTION
        )
        asyncio.run(service.delete_document(TENANT, doc_id))

        rows = Database().fetch(
            "SELECT chunk_id, doc_id, chunk_content_hash FROM citations WHERE citation_id = $1",
            CITATION,
        )
        assert len(rows) == 1
        assert rows[0]["chunk_id"] is None
        assert rows[0]["doc_id"] == doc_id
        assert rows[0]["chunk_content_hash"] == "snapshot-hash"
    finally:
        _cleanup()

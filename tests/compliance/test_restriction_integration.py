"""FR-12-18 限制处理与保留期到期自动清除。"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta

import pytest

from kb.compliance.erasure import (
    DocumentErasureService,
    PostgresErasureRepository,
    request_id_for,
)
from kb.compliance.restriction import RestrictionService
from kb.database import Database
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
SOURCE = "src_hold_it"
EXTERNAL = "ext-hold-1"
DOC = document_id(SOURCE, EXTERNAL)
COLLECTION = "hold_it"


def _cleanup() -> None:
    db = Database()
    db.execute("DELETE FROM restriction_holds WHERE tenant_id = $1", TENANT)
    db.execute(
        "DELETE FROM erasure_certificates WHERE request_id = $1",
        request_id_for(TENANT, DOC),
    )
    db.execute(
        "DELETE FROM erasure_scopes WHERE request_id = $1",
        request_id_for(TENANT, DOC),
    )
    db.execute(
        "DELETE FROM erasure_requests WHERE request_id = $1",
        request_id_for(TENANT, DOC),
    )
    db.execute(
        "DELETE FROM suppression_list WHERE tenant_id = $1 AND scope_type = 'document'",
        TENANT,
    )
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


def _index() -> None:
    Database().execute(
        """
        INSERT INTO sources (source_id, tenant_id, source_type, display_name)
        VALUES ($1,$2,'upload','限制处理集成测试')
        ON CONFLICT (source_id) DO NOTHING
        """,
        SOURCE,
        TENANT,
    )
    pipeline = IndexingPipeline(
        FakeConnector([FakeDoc(EXTERNAL, "保留期内的内容不应被删除。")]),
        PostgresDocumentStore(),
        tenant_id=TENANT,
        source_id=SOURCE,
        chunking=ChunkingConfig(target_tokens=10, max_tokens=20, min_tokens=3, overlap_ratio=0.0),
        vector_indexer=VectorIndexer(
            HashingEmbedder(dimension=16),
            InMemoryVectorStore(),
            collection=COLLECTION,
            vector_store_name="memory",
        ),
    )
    asyncio.run(pipeline.run())


def test_active_hold_restricts_then_expiry_purges():
    _cleanup()
    try:
        _index()
        restrictions = RestrictionService()
        now = datetime.now(UTC)
        asyncio.run(
            restrictions.hold_document(
                TENANT,
                DOC,
                reason="statutory_retention",
                statutory_until=now + timedelta(days=1),
            )
        )
        db = Database()
        status = db.fetch("SELECT status FROM documents WHERE doc_id = $1", DOC)[0]["status"]
        assert status == "restricted"

        eraser = DocumentErasureService(
            PostgresErasureRepository(),
            PostgresDocumentStore(),
            InMemoryVectorStore(),
            InMemoryQueryCache(),
            scope_key_hasher=lambda value: hashlib.sha256(value.encode()).hexdigest(),
            collection=COLLECTION,
            restrictions=restrictions,
        )
        result = asyncio.run(eraser.erase_document(TENANT, DOC))

        assert result.state == "RESTRICTED"
        assert db.fetch("SELECT 1 FROM documents WHERE doc_id = $1", DOC)
        assert db.fetch("SELECT 1 FROM chunks WHERE doc_id = $1", DOC)
        assert (
            db.fetch(
                "SELECT 1 FROM erasure_certificates WHERE request_id = $1",
                request_id_for(TENANT, DOC),
            )
            == []
        )

        results = asyncio.run(restrictions.purge_expired(eraser, now + timedelta(days=2)))
        assert len(results) == 1
        assert results[0].state == "CLOSED"
        assert db.fetch("SELECT 1 FROM documents WHERE doc_id = $1", DOC) == []
        assert db.fetch("SELECT 1 FROM restriction_holds WHERE tenant_id = $1", TENANT) == []
        assert db.fetch(
            "SELECT 1 FROM erasure_certificates WHERE request_id = $1",
            request_id_for(TENANT, DOC),
        )
        suppression = db.fetch(
            "SELECT 1 FROM suppression_list WHERE tenant_id = $1 AND scope_type = 'document'",
            TENANT,
        )
        assert suppression
    finally:
        _cleanup()

"""混合检索（词法 + 向量 + 融合 + 二次校验）的集成测试。

需要 `make up`。只验证两件事：
1. 召回阶段已按权限过滤（默认拒绝的文档不出现）。
2. 允许的文档能被词法/向量任一腿召回并经融合返回。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from kb.database import Database
from kb.identity.permissions import FULL_SCOPE, DelegationContext, Principal
from kb.indexing.embedding import HashingEmbedder
from kb.indexing.ids import document_id
from kb.indexing.pipeline import IndexingPipeline
from kb.indexing.vector_store import InMemoryVectorStore
from kb.parsing.chunking import ChunkingConfig
from kb.retrieval.models import RetrievalConfig
from kb.retrieval.pg_adapters import HybridRetrievalService
from tests.indexing.fakes import FakeConnector, FakeDoc

pytestmark = pytest.mark.integration

TENANT = "default"
SOURCE = "src_hybrid_it"
COLLECTION = "hybrid_it"
DOC_OK = "doc-ok"
DOC_DENIED = "doc-denied"
QUERY = "量子咖啡机"


def _ctx() -> DelegationContext:
    now = datetime.now(UTC)
    return DelegationContext(
        principal=Principal("user", "alice"),
        actor=None,
        scope=FULL_SCOPE,
        assurance_level="L2",
        expires_at=now + timedelta(hours=1),
        evidence={},
    )


def _cleanup() -> None:
    db = Database()
    db.execute("DELETE FROM acl_entries WHERE source_id = $1", SOURCE)
    for external in (DOC_OK, DOC_DENIED):
        db.execute(
            """
            DELETE FROM chunk_vectors cv USING chunks c
            WHERE c.chunk_id = cv.chunk_id AND c.doc_id = $1
            """,
            document_id(SOURCE, external),
        )
        db.execute("DELETE FROM chunks WHERE doc_id = $1", document_id(SOURCE, external))
    db.execute("DELETE FROM documents WHERE source_id = $1", SOURCE)
    db.execute("DELETE FROM sources WHERE source_id = $1", SOURCE)


def _seed_source() -> None:
    Database().execute(
        """
        INSERT INTO sources (source_id, tenant_id, source_type, display_name)
        VALUES ($1, $2, 'upload', '混合检索集成测试')
        ON CONFLICT (source_id) DO NOTHING
        """,
        SOURCE,
        TENANT,
    )


def test_hybrid_retrieval_filters_permissions_at_recall():
    from kb.ingestion.contract import AclSet

    _cleanup()
    _seed_source()
    try:
        allowed = FakeDoc(DOC_OK, "量子咖啡机的报销标准与流程。")
        denied = FakeDoc(
            DOC_DENIED,
            "量子咖啡机的报销标准与流程。",
            acl=AclSet(
                entries=(),
                inherited_from=None,
                inheritance_broken=False,
                fetched_at=datetime.now(UTC),
            ),
        )
        embedder = HashingEmbedder(dimension=32)
        vectors = InMemoryVectorStore()
        from kb.indexing.vector_index import VectorIndexer

        pipeline = IndexingPipeline(
            FakeConnector([allowed, denied]),
            _pg_store(),
            tenant_id=TENANT,
            source_id=SOURCE,
            chunking=ChunkingConfig(
                target_tokens=20, max_tokens=40, min_tokens=5, overlap_ratio=0.0
            ),
            vector_indexer=VectorIndexer(
                embedder, vectors, collection=COLLECTION, vector_store_name="memory"
            ),
        )
        stats = asyncio.run(pipeline.run())
        assert stats.indexed == 2

        service = HybridRetrievalService(
            Database(),
            vectors,
            embedder,
            tenant_id=TENANT,
            collection=COLLECTION,
            config=RetrievalConfig(
                audience_allowlist=frozenset({"internal_only", "customer_safe"})
            ),
        )
        results = asyncio.run(service.retrieve(_ctx(), QUERY, k=10))

        doc_ids = {item.record.doc_id for item in results}
        assert document_id(SOURCE, DOC_OK) in doc_ids
        assert document_id(SOURCE, DOC_DENIED) not in doc_ids
        assert all(item.score > 0 for item in results)
    finally:
        _cleanup()


def _pg_store():
    from kb.indexing.store import PostgresDocumentStore

    return PostgresDocumentStore()

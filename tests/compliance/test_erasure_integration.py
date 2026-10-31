"""FR-12-04 文档级贯穿式删除集成测试。"""

from __future__ import annotations

import asyncio
import hashlib
import json

import pytest

from kb.compliance.backup import TrackingBackupRotation
from kb.compliance.erasure import (
    DocumentErasureService,
    PostgresErasureRepository,
    request_id_for,
)
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
SOURCE = "src_erase_it"
EXTERNAL = "ext-erase-1"
COLLECTION = "erase_it"
DOC = document_id(SOURCE, EXTERNAL)
SESSION = "sess_erase_it"
QUERY = "qry_erase_it"
ANSWER = "ans_erase_it"
CITATION = "cit_erase_it"
QA = "qa_erase_it"
SET = "set_erase_it"
CASE = "case_erase_it"


def _hasher(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _cleanup() -> None:
    db = Database()
    db.execute(
        "DELETE FROM erasure_certificates WHERE request_id = $1", request_id_for(TENANT, DOC)
    )
    db.execute("DELETE FROM erasure_scopes WHERE request_id = $1", request_id_for(TENANT, DOC))
    db.execute("DELETE FROM erasure_requests WHERE request_id = $1", request_id_for(TENANT, DOC))
    db.execute(
        "DELETE FROM suppression_list WHERE tenant_id = $1 "
        "AND scope_type = 'document' AND scope_key = $2",
        TENANT,
        _hasher(f"{SOURCE}\x00{EXTERNAL}"),
    )
    db.execute("DELETE FROM curated_qa WHERE qa_id = $1", QA)
    db.execute("DELETE FROM eval_cases WHERE case_id = $1", CASE)
    db.execute("DELETE FROM eval_sets WHERE set_id = $1", SET)
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
        DOC,
    )
    db.execute("DELETE FROM chunks WHERE doc_id = $1", DOC)
    db.execute("DELETE FROM annotations WHERE doc_id = $1", DOC)
    db.execute("DELETE FROM documents WHERE source_id = $1", SOURCE)
    db.execute("DELETE FROM sources WHERE source_id = $1", SOURCE)


def _seed_related(chunk_id: str) -> None:
    db = Database()
    db.execute(
        "INSERT INTO sessions (session_id, tenant_id, principal_id, channel) "
        "VALUES ($1,$2,'alice','web')",
        SESSION,
        TENANT,
    )
    db.execute(
        """
        INSERT INTO queries
            (query_id, tenant_id, session_id, principal_id, channel, text_hash, text_length)
        VALUES ($1,$2,$3,'alice','web','h',4)
        """,
        QUERY,
        TENANT,
        SESSION,
    )
    db.execute(
        """
        INSERT INTO answers
            (answer_id, tenant_id, query_id, text, model_name, model_version, latency_ms)
        VALUES ($1,$2,$3,'答案[1]。','test','v1',10)
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
        VALUES ($1,$2,$3,0,$4,$5,'snap','1','1')
        """,
        CITATION,
        TENANT,
        ANSWER,
        chunk_id,
        DOC,
    )
    db.execute(
        """
        INSERT INTO curated_qa
            (qa_id, tenant_id, question_pattern, answer_text, citations, owner_id)
        VALUES ($1,$2,'问','答',$3::jsonb,'expert')
        """,
        QA,
        TENANT,
        json.dumps([{"chunk_id": chunk_id, "doc_id": DOC}]),
    )
    db.execute(
        "INSERT INTO eval_sets (set_id, tenant_id, name, version) VALUES ($1,$2,'s','1')",
        SET,
        TENANT,
    )
    db.execute(
        """
        INSERT INTO eval_cases
            (case_id, tenant_id, set_id, question, expected_chunk_ids, pii_redacted)
        VALUES ($1,$2,$3,'问',ARRAY[$4]::text[],true)
        """,
        CASE,
        TENANT,
        SET,
        chunk_id,
    )


def test_document_erasure_covers_ten_locations_and_is_idempotent():
    _cleanup()
    Database().execute(
        """
        INSERT INTO sources (source_id, tenant_id, source_type, display_name)
        VALUES ($1,$2,'upload','删除链路集成测试')
        ON CONFLICT (source_id) DO NOTHING
        """,
        SOURCE,
        TENANT,
    )
    try:
        vectors = InMemoryVectorStore()
        store = PostgresDocumentStore()
        pipeline = IndexingPipeline(
            FakeConnector([FakeDoc(EXTERNAL, "删除链路测试正文：量子咖啡机报销。")]),
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
        chunk_ids = asyncio.run(store.list_chunk_ids(TENANT, DOC))
        assert chunk_ids
        _seed_related(chunk_ids[0])

        cache = InMemoryQueryCache()
        cache.put("q", chunk_ids)
        service = DocumentErasureService(
            PostgresErasureRepository(),
            store,
            vectors,
            cache,
            scope_key_hasher=_hasher,
            collection=COLLECTION,
            backup=TrackingBackupRotation(rotation_days=90),
        )
        result = asyncio.run(
            service.erase_document(TENANT, DOC, executor_id="ops", reviewer_id="compliance")
        )

        assert result.state == "CLOSED"
        assert len(result.outcomes) == 10
        statuses = {o.location_type: o.status for o in result.outcomes}
        assert statuses["chunk"] == "PHYSICAL_DONE"
        assert statuses["vector"] == "PHYSICAL_DONE"
        assert statuses["cache"] == "PHYSICAL_DONE"
        assert statuses["curated_qa"] == "PHYSICAL_DONE"
        assert statuses["eval_case"] == "PHYSICAL_DONE"
        assert statuses["audit"] == "SKIPPED"
        assert statuses["backup"] == "LOGICAL_DONE"
        assert statuses["raw_object"] == "SKIPPED"

        db = Database()
        assert db.fetch("SELECT 1 FROM documents WHERE doc_id = $1", DOC) == []
        assert db.fetch("SELECT 1 FROM chunks WHERE doc_id = $1", DOC) == []
        assert cache.get("q") is None
        citation = db.fetch("SELECT chunk_id FROM citations WHERE citation_id = $1", CITATION)
        assert citation[0]["chunk_id"] is None
        qa = db.fetch("SELECT review_state FROM curated_qa WHERE qa_id = $1", QA)
        assert qa[0]["review_state"] == "needs_review"
        case = db.fetch("SELECT expected_chunk_ids FROM eval_cases WHERE case_id = $1", CASE)
        assert case[0]["expected_chunk_ids"] == []
        suppression = db.fetch(
            "SELECT 1 FROM suppression_list WHERE tenant_id = $1 AND scope_key = $2",
            TENANT,
            _hasher(f"{SOURCE}\x00{EXTERNAL}"),
        )
        assert suppression
        request = db.fetch(
            "SELECT state FROM erasure_requests WHERE request_id = $1", request_id_for(TENANT, DOC)
        )
        assert request[0]["state"] == "CLOSED"
        scopes = db.fetch(
            "SELECT count(*) AS n FROM erasure_scopes WHERE request_id = $1",
            request_id_for(TENANT, DOC),
        )
        assert scopes[0]["n"] == 10
        cert = db.fetch(
            "SELECT uncovered_scope_statement, cert_hash FROM erasure_certificates "
            "WHERE request_id = $1",
            request_id_for(TENANT, DOC),
        )
        assert cert
        assert cert[0]["uncovered_scope_statement"]
        assert cert[0]["cert_hash"]

        again = asyncio.run(service.erase_document(TENANT, DOC))
        assert again.state == "CLOSED"
        assert again.outcomes == ()
    finally:
        _cleanup()

"""T-1 主体删除：subject_index + 兜底检索 + 正文脱敏。"""

from __future__ import annotations

import asyncio
import json

import pytest

from kb.compliance.backup import TrackingBackupRotation
from kb.compliance.crypto import KeyDestroyedError, SoftwareKeyManager, kek_from_passphrase
from kb.compliance.pii import detect_pii, subject_key
from kb.compliance.subject_erasure import SubjectErasureService
from kb.compliance.subject_index import PostgresSubjectIndexer
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
SOURCE = "src_subj_it"
EXTERNAL = "ext-subj-1"
DOC = document_id(SOURCE, EXTERNAL)
PHONE = "13800138000"
EMAIL = "alice@example.com"
EMAIL_IN_DB = "Alice@Example.com"
PEPPER = "test-pepper"
COLLECTION = "subj_it"
SESSION = "sess_subj_it"
QUERY = "qry_subj_it"
ANSWER = "ans_subj_it"
QA = "qa_subj_it"
SET = "set_subj_it"
CASE = "case_subj_it"


def _key() -> str:
    return subject_key(PHONE, PEPPER)


def _email_key() -> str:
    return subject_key(EMAIL, PEPPER)


def _cleanup() -> None:
    db = Database()
    for key in (_key(), _email_key()):
        db.execute(
            "DELETE FROM subject_index WHERE tenant_id = $1 AND subject_key = $2", TENANT, key
        )
    db.execute("DELETE FROM subject_keys WHERE tenant_id = $1 AND subject_key = $2", TENANT, _key())
    db.execute("DELETE FROM erasure_certificates WHERE tenant_id = $1", TENANT)
    db.execute("DELETE FROM erasure_scopes WHERE tenant_id = $1", TENANT)
    db.execute("DELETE FROM erasure_requests WHERE tenant_id = $1", TENANT)
    for key in (_key(), _email_key()):
        db.execute(
            "DELETE FROM suppression_list WHERE tenant_id = $1 "
            "AND scope_type = 'subject' AND scope_key = $2",
            TENANT,
            key,
        )
    db.execute("DELETE FROM curated_qa WHERE qa_id = $1", QA)
    db.execute("DELETE FROM eval_cases WHERE case_id = $1", CASE)
    db.execute("DELETE FROM eval_sets WHERE set_id = $1", SET)
    db.execute("DELETE FROM answers WHERE answer_id = $1", ANSWER)
    db.execute("DELETE FROM queries WHERE query_id = $1", QUERY)
    db.execute("DELETE FROM sessions WHERE session_id = $1", SESSION)
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


def test_subject_erasure_removes_chunks_and_redacts_texts():
    _cleanup()
    Database().execute(
        """
        INSERT INTO sources (source_id, tenant_id, source_type, display_name)
        VALUES ($1,$2,'upload','主体删除集成测试')
        ON CONFLICT (source_id) DO NOTHING
        """,
        SOURCE,
        TENANT,
    )
    try:
        vectors = InMemoryVectorStore()
        store = PostgresDocumentStore()
        indexer = PostgresSubjectIndexer(PEPPER)
        pipeline = IndexingPipeline(
            FakeConnector([FakeDoc(EXTERNAL, f"员工张三联系电话{PHONE}，请勿外传。")]),
            store,
            tenant_id=TENANT,
            source_id=SOURCE,
            chunking=ChunkingConfig(
                target_tokens=20, max_tokens=40, min_tokens=3, overlap_ratio=0.0
            ),
            vector_indexer=VectorIndexer(
                HashingEmbedder(dimension=16),
                vectors,
                collection=COLLECTION,
                vector_store_name="memory",
            ),
            subject_indexer=indexer,
        )
        asyncio.run(pipeline.run())
        chunk_ids = asyncio.run(store.list_chunk_ids(TENANT, DOC))
        assert chunk_ids
        assert asyncio.run(indexer.locations(TENANT, [_key()]))

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
                (query_id, tenant_id, session_id, principal_id, channel,
                 raw_text, text_hash, text_length)
            VALUES ($1,$2,$3,'alice','web',$4,'h',10)
            """,
            QUERY,
            TENANT,
            SESSION,
            f"张三的电话是{PHONE}",
        )
        db.execute(
            """
            INSERT INTO answers
                (answer_id, tenant_id, query_id, text, model_name, model_version, latency_ms)
            VALUES ($1,$2,$3,$4,'test','v1',10)
            """,
            ANSWER,
            TENANT,
            QUERY,
            f"电话是{PHONE}[1]，备用邮箱 {EMAIL_IN_DB}。",
        )
        db.execute(
            """
            INSERT INTO curated_qa
                (qa_id, tenant_id, question_pattern, answer_text, citations, owner_id)
            VALUES ($1,$2,'联系方式',$3,$4::jsonb,'expert')
            """,
            QA,
            TENANT,
            f"请拨打{PHONE}",
            json.dumps([]),
        )
        db.execute(
            "INSERT INTO eval_sets (set_id, tenant_id, name, version) VALUES ($1,$2,'s','1')",
            SET,
            TENANT,
        )
        db.execute(
            """
            INSERT INTO eval_cases
                (case_id, tenant_id, set_id, question, expected_answer, pii_redacted)
            VALUES ($1,$2,$3,$4,'答',true)
            """,
            CASE,
            TENANT,
            SET,
            f"张三的电话{PHONE}?",
        )

        asyncio.run(indexer.index_location(TENANT, "session", SESSION, f"张三的电话是{PHONE}"))
        asyncio.run(
            indexer.index_location(
                TENANT, "answer", ANSWER, f"电话是{PHONE}[1]，备用邮箱 {EMAIL_IN_DB}。"
            )
        )
        asyncio.run(indexer.index_location(TENANT, "curated_qa", QA, f"请拨打{PHONE}"))
        asyncio.run(indexer.index_location(TENANT, "eval_case", CASE, f"张三的电话{PHONE}?"))

        cache = InMemoryQueryCache()
        cache.put("q", chunk_ids)
        key_manager = SoftwareKeyManager(kek_from_passphrase("test-kek"))
        asyncio.run(key_manager.register(TENANT, _key()))
        ciphertext = asyncio.run(key_manager.encrypt(TENANT, _key(), f"secret {PHONE}"))
        service = SubjectErasureService(
            store,
            vectors,
            cache,
            indexer,
            pepper=PEPPER,
            collection=COLLECTION,
            key_manager=key_manager,
            backup=TrackingBackupRotation(rotation_days=90),
        )
        result = asyncio.run(
            service.erase_subject(
                TENANT,
                [*detect_pii(PHONE), *detect_pii(EMAIL)],
                executor_id="ops",
                reviewer_id="compliance",
            )
        )

        assert result.state == "CLOSED"
        statuses = {o.location_type: o.status for o in result.outcomes}
        assert statuses["audit"] == "LOGICAL_DONE"
        assert statuses["backup"] == "LOGICAL_DONE"
        destroyed = db.fetch(
            "SELECT destroyed_at FROM subject_keys WHERE tenant_id = $1 AND subject_key = $2",
            TENANT,
            _key(),
        )
        assert destroyed and destroyed[0]["destroyed_at"] is not None
        with pytest.raises(KeyDestroyedError):
            asyncio.run(key_manager.decrypt(TENANT, _key(), ciphertext))
        assert db.fetch("SELECT 1 FROM chunks WHERE doc_id = $1", DOC) == []
        assert cache.get("q") is None
        assert (
            db.fetch(
                "SELECT 1 FROM subject_index WHERE tenant_id = $1 AND subject_key = $2",
                TENANT,
                _key(),
            )
            == []
        )
        answer_text = str(
            db.fetch("SELECT text FROM answers WHERE answer_id = $1", ANSWER)[0]["text"]
        )
        assert PHONE not in answer_text
        assert EMAIL_IN_DB not in answer_text
        assert "[已依法删除]" in answer_text
        query_text = str(
            db.fetch("SELECT raw_text FROM queries WHERE query_id = $1", QUERY)[0]["raw_text"]
        )
        assert PHONE not in query_text
        assert "[已依法删除]" in query_text
        qa = db.fetch("SELECT answer_text, review_state FROM curated_qa WHERE qa_id = $1", QA)[0]
        assert PHONE not in str(qa["answer_text"])
        assert qa["review_state"] == "needs_review"
        assert db.fetch("SELECT 1 FROM eval_cases WHERE case_id = $1", CASE) == []
        assert db.fetch(
            "SELECT 1 FROM suppression_list WHERE tenant_id = $1 "
            "AND scope_type = 'subject' AND scope_key = $2",
            TENANT,
            _key(),
        )
        cert = db.fetch(
            "SELECT uncovered_scope_statement FROM erasure_certificates WHERE request_id = $1",
            result.request_id,
        )
        assert cert and cert[0]["uncovered_scope_statement"]

        again = asyncio.run(service.erase_subject(TENANT, [*detect_pii(PHONE), *detect_pii(EMAIL)]))
        assert again.state == "CLOSED"
        assert again.outcomes == ()
    finally:
        _cleanup()

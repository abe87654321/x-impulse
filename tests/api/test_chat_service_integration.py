"""对话持久化与审计：会话/查询/答案/引用/哈希链。"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest

from kb.api.chat_service import ChatService
from kb.audit.writer import PostgresAuditStore
from kb.database import Database
from kb.generation.citations import Answer, BoundSentence, Citation
from kb.identity.permissions import FULL_SCOPE, DelegationContext, Principal

pytestmark = pytest.mark.integration

TENANT = "t_api_it"


class _Answers:
    async def answer(self, ctx, question):  # noqa: ANN001, ANN201
        citation = Citation(
            sentence_index=0,
            chunk_id="chk_api",
            doc_id="doc_api",
            page_no=1,
            section_path="A",
            clause_no="1",
            char_start=0,
            char_end=10,
            chunk_content_hash="hash",
            chunk_version="1",
            doc_version="1",
        )
        sentence = BoundSentence(sentence_index=0, text="答案。", citations=(citation,))
        return Answer(
            text="答案。",
            sentences=(sentence,),
            citations=(citation,),
            refused=False,
            refusal_reason=None,
        )


def _ctx() -> DelegationContext:
    now = datetime.now(UTC)
    return DelegationContext(
        principal=Principal("user", "alice"),
        actor=None,
        scope=FULL_SCOPE,
        assurance_level="L2",
        expires_at=now + timedelta(minutes=5),
        evidence={},
    )


def _cleanup() -> None:
    db = Database()
    db.execute("DELETE FROM audit_log WHERE tenant_id = $1", TENANT)
    db.execute("DELETE FROM citations WHERE tenant_id = $1", TENANT)
    db.execute("DELETE FROM answers WHERE tenant_id = $1", TENANT)
    db.execute("DELETE FROM queries WHERE tenant_id = $1", TENANT)
    db.execute("DELETE FROM sessions WHERE tenant_id = $1", TENANT)
    db.execute("DELETE FROM chunks WHERE tenant_id = $1", TENANT)
    db.execute("DELETE FROM documents WHERE tenant_id = $1", TENANT)
    db.execute("DELETE FROM sources WHERE tenant_id = $1", TENANT)
    db.execute("DELETE FROM tenants WHERE tenant_id = $1", TENANT)


def _seed_chunk() -> None:
    db = Database()
    db.execute(
        "INSERT INTO sources (source_id, tenant_id, source_type, display_name) "
        f"VALUES ('src_api','{TENANT}','upload','api') ON CONFLICT (source_id) DO NOTHING"
    )
    db.execute(
        """
        INSERT INTO documents
            (doc_id, tenant_id, source_id, external_id, title, content_hash,
             parser_version, status)
        VALUES ('doc_api', $1, 'src_api', 'ext-api', 'doc', 'h', 'text-v1', 'indexed')
        ON CONFLICT (doc_id) DO NOTHING
        """,
        TENANT,
    )
    db.execute(
        """
        INSERT INTO chunks
            (chunk_id, tenant_id, doc_id, seq, content, content_hash, token_count,
             char_start, char_end, chunker_version)
        VALUES ('chk_api', $1, 'doc_api', 0, '内容', 'hash', 1, 0, 2, 'chunk-v1')
        ON CONFLICT (chunk_id) DO NOTHING
        """,
        TENANT,
    )


def test_chat_persists_and_audits_without_raw_text():
    _cleanup()
    Database().execute(
        "INSERT INTO tenants (tenant_id, display_name) VALUES ($1,'api-test')", TENANT
    )
    _seed_chunk()
    try:
        service = ChatService(_Answers(), audit=PostgresAuditStore())
        result = asyncio.run(service.ask(_ctx(), TENANT, "报销标准是什么？", session_id="sess_api"))

        assert result.answer.text == "答案。"
        db = Database()
        session = db.fetch("SELECT session_id FROM sessions WHERE tenant_id = $1", TENANT)
        assert session[0]["session_id"] == "sess_api"
        query = db.fetch(
            "SELECT raw_text, text_hash, text_length FROM queries WHERE query_id = $1",
            result.query_id,
        )[0]
        assert query["raw_text"] is None
        assert query["text_hash"] == hashlib.sha256("报销标准是什么？".encode()).hexdigest()
        assert query["text_length"] == len("报销标准是什么？")
        answer = db.fetch(
            "SELECT refused, text FROM answers WHERE answer_id = $1", result.answer_id
        )[0]
        assert answer["refused"] is False
        citations = db.fetch(
            "SELECT chunk_id, chunk_content_hash, chunk_version, doc_version "
            "FROM citations WHERE answer_id = $1",
            result.answer_id,
        )
        assert citations == [
            {
                "chunk_id": "chk_api",
                "chunk_content_hash": "hash",
                "chunk_version": "1",
                "doc_version": "1",
            }
        ]
        logs = db.fetch(
            "SELECT category, prev_hash, row_hash, payload FROM audit_log "
            "WHERE tenant_id = $1 ORDER BY log_id",
            TENANT,
        )
        assert [row["category"] for row in logs] == ["query", "answer"]
        assert logs[0]["prev_hash"] is None
        assert logs[1]["prev_hash"] == logs[0]["row_hash"]
        assert "报销标准" not in json.dumps(logs, ensure_ascii=False, default=str)
    finally:
        _cleanup()

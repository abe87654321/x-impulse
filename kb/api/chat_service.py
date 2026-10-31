"""对话服务 —— 会话、查询、答案、引用的持久化与审计。

FR-09-01 × FR-12-16：默认不落问题原文（raw_text 保持 NULL），
只存归一化哈希与长度。审计只记录 query_id / text_hash，不记录原文。

FR-16：每次提问先过分层限流；召回后做批量抽取检测。限流状态由
`RateLimiter` 注入，默认内存实现仅适合单进程开发。
"""

from __future__ import annotations

import asyncio
import hashlib
import time
import uuid
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

import asyncpg

from kb.audit.writer import AuditEvent, AuditStore
from kb.compliance.subject_index import SubjectIndexer
from kb.database import dsn_from_env
from kb.generation.answer_service import AnswerService
from kb.generation.citations import DEFAULT_REFUSAL, Answer, BoundSentence, Citation
from kb.governance.metering import InMemoryUsageMeter, UsageMeter, usage_record
from kb.governance.rate_limit import ExtractionSignal, RateLimited, RateLimiter
from kb.identity.permissions import DelegationContext
from kb.retrieval.models import RetrievedChunk

_SESSION_SQL = """
INSERT INTO sessions (session_id, tenant_id, principal_id, channel)
VALUES ($1, $2, $3, $4)
ON CONFLICT (session_id) DO UPDATE SET last_active_at = now()
"""

_QUERY_SQL = """
INSERT INTO queries
    (query_id, tenant_id, session_id, principal_id, actor_id, assurance_level,
     channel, text_hash, text_length)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
"""

_ANSWER_SQL = """
INSERT INTO answers
    (answer_id, tenant_id, query_id, text, refused, refusal_reason,
     model_name, model_version, latency_ms, ttft_ms)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
"""

_CITATION_SQL = """
INSERT INTO citations
    (citation_id, tenant_id, answer_id, sentence_index, chunk_id, doc_id,
     page_no, section_path, clause_no, char_start, char_end,
     chunk_content_hash, chunk_version, doc_version)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
"""


@dataclass(frozen=True)
class ChatResult:
    session_id: str
    query_id: str
    answer_id: str
    answer: Answer


@dataclass(frozen=True)
class ChatStreamEvent:
    kind: Literal["sentence", "refusal", "done", "error"]
    text: str = ""
    sentence_index: int | None = None
    citations: tuple[Citation, ...] = ()
    session_id: str | None = None
    query_id: str | None = None
    answer_id: str | None = None
    refused: bool | None = None
    error: str | None = None


class ChatService:
    def __init__(
        self,
        answers: AnswerService,
        *,
        audit: AuditStore | None = None,
        model_name: str = "unknown",
        model_version: str = "v1",
        dsn: str | None = None,
        rate_limiter: RateLimiter | None = None,
        meter: UsageMeter | None = None,
        subject_indexer: SubjectIndexer | None = None,
    ) -> None:
        self._answers = answers
        self._audit = audit
        self._model_name = model_name
        self._model_version = model_version
        self._dsn = dsn or dsn_from_env()
        self._limiter = rate_limiter or RateLimiter(audit=audit)
        self._meter: UsageMeter = meter or InMemoryUsageMeter()
        self._subject_indexer = subject_indexer

    async def ask(
        self,
        ctx: DelegationContext,
        tenant_id: str,
        question: str,
        *,
        session_id: str | None = None,
        channel: str = "api",
    ) -> ChatResult:
        now = datetime.now(UTC)
        session = session_id or f"sess_{uuid.uuid4().hex}"
        decision = await self._limiter.check(
            ctx, tenant_id=tenant_id, channel=channel, session_id=session, now=now
        )
        if not decision.allowed:
            raise RateLimited(decision)
        if decision.delay_seconds:
            await asyncio.sleep(decision.delay_seconds)

        query_id = f"qry_{uuid.uuid4().hex}"
        answer_id = f"ans_{uuid.uuid4().hex}"
        await self._begin_query(ctx, tenant_id, session, query_id, question, channel)

        started = time.monotonic()
        result = await self._answers.answer_with_evidence(ctx, question)
        await self._record_recall(ctx, tenant_id, session, result.evidence, now)
        latency_ms = int((time.monotonic() - started) * 1000)

        await self._persist_answer(
            tenant_id, query_id, answer_id, result.answer, latency_ms, ttft_ms=None
        )
        await self._index_answer(tenant_id, answer_id, result.answer)
        self._record_usage(ctx, tenant_id, result.answer)
        await self._audit_events(
            ctx, tenant_id, question, session, query_id, answer_id, result.answer
        )
        return ChatResult(
            session_id=session, query_id=query_id, answer_id=answer_id, answer=result.answer
        )

    async def ask_stream(
        self,
        ctx: DelegationContext,
        tenant_id: str,
        question: str,
        *,
        session_id: str | None = None,
        channel: str = "api",
    ) -> AsyncIterator[ChatStreamEvent]:
        """SSE 流：逐句输出已通过引用绑定校验的句子（FR-05-12 / FR-05-05a）。"""
        now = datetime.now(UTC)
        session = session_id or f"sess_{uuid.uuid4().hex}"
        decision = await self._limiter.check(
            ctx, tenant_id=tenant_id, channel=channel, session_id=session, now=now
        )
        if not decision.allowed:
            raise RateLimited(decision)
        if decision.delay_seconds:
            await asyncio.sleep(decision.delay_seconds)

        query_id = f"qry_{uuid.uuid4().hex}"
        answer_id = f"ans_{uuid.uuid4().hex}"
        await self._begin_query(ctx, tenant_id, session, query_id, question, channel)

        prepared = await self._answers.prepare_stream(ctx, question)
        signal = await self._record_recall(ctx, tenant_id, session, prepared.evidence, now)
        if signal.alert and self._limiter.config.slowdown_seconds:
            await asyncio.sleep(self._limiter.config.slowdown_seconds)

        started = time.monotonic()
        ttft_ms: int | None = None
        sentences: list[BoundSentence] = []
        citations: list[Citation] = []
        refusal_text: str | None = None

        try:
            async for event in prepared.events:
                if event.kind == "sentence":
                    if ttft_ms is None:
                        ttft_ms = int((time.monotonic() - started) * 1000)
                    bound = BoundSentence(
                        sentence_index=event.sentence_index or 0,
                        text=event.text,
                        citations=event.citations,
                    )
                    sentences.append(bound)
                    citations.extend(event.citations)
                    yield ChatStreamEvent(
                        kind="sentence",
                        text=event.text,
                        sentence_index=event.sentence_index,
                        citations=event.citations,
                    )
                else:
                    refusal_text = event.text
                    yield ChatStreamEvent(kind="refusal", text=event.text)
        except Exception as exc:  # noqa: BLE001
            yield ChatStreamEvent(kind="error", error=str(exc) or "generation_failed")
            return

        answer = _assemble_stream_answer(
            prepared_evidence=bool(prepared.evidence),
            sentences=sentences,
            citations=citations,
            refusal_text=refusal_text,
        )
        latency_ms = int((time.monotonic() - started) * 1000)
        await self._persist_answer(
            tenant_id, query_id, answer_id, answer, latency_ms, ttft_ms=ttft_ms
        )
        await self._index_answer(tenant_id, answer_id, answer)
        self._record_usage(ctx, tenant_id, answer)
        await self._audit_events(ctx, tenant_id, question, session, query_id, answer_id, answer)
        yield ChatStreamEvent(
            kind="done",
            session_id=session,
            query_id=query_id,
            answer_id=answer_id,
            refused=answer.refused,
            citations=answer.citations,
        )

    async def _index_answer(self, tenant_id: str, answer_id: str, answer: Answer) -> None:
        if self._subject_indexer is None:
            return
        await self._subject_indexer.index_location(tenant_id, "answer", answer_id, answer.text)

    async def _begin_query(
        self,
        ctx: DelegationContext,
        tenant_id: str,
        session: str,
        query_id: str,
        question: str,
        channel: str,
    ) -> None:
        principal_id = ctx.principal.id if ctx.principal else "anonymous"
        actor_id = ctx.actor.id if ctx.actor else None
        text_hash = hashlib.sha256(question.encode("utf-8")).hexdigest()

        conn = await asyncpg.connect(self._dsn)
        try:
            async with conn.transaction():
                await conn.execute(_SESSION_SQL, session, tenant_id, principal_id, channel)
                await conn.execute(
                    _QUERY_SQL,
                    query_id,
                    tenant_id,
                    session,
                    principal_id,
                    actor_id,
                    ctx.assurance_level,
                    channel,
                    text_hash,
                    len(question),
                )
        finally:
            await conn.close()

    async def _record_recall(
        self,
        ctx: DelegationContext,
        tenant_id: str,
        session: str,
        evidence: Sequence[RetrievedChunk],
        now: datetime,
    ) -> ExtractionSignal:
        chunks = [(item.record.chunk_id, item.record.doc_id) for item in evidence]
        return await self._limiter.record_recall(
            ctx, tenant_id=tenant_id, session_id=session, chunks=chunks, now=now
        )

    async def _persist_answer(
        self,
        tenant_id: str,
        query_id: str,
        answer_id: str,
        answer: Answer,
        latency_ms: int,
        *,
        ttft_ms: int | None,
    ) -> None:
        conn = await asyncpg.connect(self._dsn)
        try:
            async with conn.transaction():
                await conn.execute(
                    _ANSWER_SQL,
                    answer_id,
                    tenant_id,
                    query_id,
                    answer.text,
                    answer.refused,
                    answer.refusal_reason,
                    self._model_name,
                    self._model_version,
                    latency_ms,
                    ttft_ms,
                )
                for citation in answer.citations:
                    await conn.execute(
                        _CITATION_SQL,
                        f"cit_{uuid.uuid4().hex}",
                        tenant_id,
                        answer_id,
                        citation.sentence_index,
                        citation.chunk_id,
                        citation.doc_id,
                        citation.page_no,
                        citation.section_path,
                        citation.clause_no,
                        citation.char_start,
                        citation.char_end,
                        citation.chunk_content_hash,
                        citation.chunk_version,
                        citation.doc_version,
                    )
        finally:
            await conn.close()

    def _record_usage(self, ctx: DelegationContext, tenant_id: str, answer: Answer) -> None:
        self._meter.record(
            usage_record(
                tenant_id=tenant_id,
                principal_id=ctx.principal.id if ctx.principal else None,
                actor_id=ctx.actor.id if ctx.actor else None,
                answer_text=answer.text,
            )
        )

    async def _audit_events(
        self,
        ctx: DelegationContext,
        tenant_id: str,
        question: str,
        session_id: str,
        query_id: str,
        answer_id: str,
        answer: Answer,
    ) -> None:
        if self._audit is None:
            return
        delegation = (ctx.actor.id,) if ctx.actor else ()
        await self._audit.append(
            AuditEvent(
                category="query",
                action="ask",
                tenant_id=tenant_id,
                principal_id=ctx.principal.id if ctx.principal else None,
                actor_id=ctx.actor.id if ctx.actor else None,
                delegation_chain=delegation,
                assurance_level=ctx.assurance_level,
                target_type="session",
                target_id=session_id,
                payload={
                    "query_id": query_id,
                    "text_hash": hashlib.sha256(question.encode("utf-8")).hexdigest(),
                    "text_length": len(question),
                },
            ),
            now=datetime.now(UTC),
        )
        await self._audit.append(
            AuditEvent(
                category="answer",
                action="refused" if answer.refused else "answered",
                tenant_id=tenant_id,
                principal_id=ctx.principal.id if ctx.principal else None,
                actor_id=ctx.actor.id if ctx.actor else None,
                delegation_chain=delegation,
                assurance_level=ctx.assurance_level,
                target_type="answer",
                target_id=answer_id,
                payload={
                    "query_id": query_id,
                    "refused": answer.refused,
                    "citation_count": len(answer.citations),
                },
            ),
            now=datetime.now(UTC),
        )


def _assemble_stream_answer(
    *,
    prepared_evidence: bool,
    sentences: list[BoundSentence],
    citations: list[Citation],
    refusal_text: str | None,
) -> Answer:
    if sentences:
        return Answer(
            text="".join(sentence.text for sentence in sentences),
            sentences=tuple(sentences),
            citations=tuple(citations),
            refused=False,
            refusal_reason=None,
        )
    return Answer(
        text=refusal_text or DEFAULT_REFUSAL,
        sentences=(),
        citations=(),
        refused=True,
        refusal_reason="no_grounded_sentence" if prepared_evidence else "no_evidence",
    )


__all__ = ["ChatResult", "ChatService", "ChatStreamEvent"]

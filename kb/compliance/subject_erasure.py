"""T-1 数据主体删除 —— 主体反向索引 + 兜底字面检索 + 正文脱敏。

覆盖范围（诚实说明）：
- chunk / vector / cache：命中主体索引或含明文标识的 chunk 直接硬删
- session / answer / curated_qa：按提供的明文标识做字面替换（[已依法删除]）
- eval_case：命中即删除
- raw_object / audit / backup：SKIPPED（与文档级删除相同的未接入基础设施）

召回上限 = PII 识别召回率 + 兜底字面检索命中率，无法宣称 100%（设计 §4.2）。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta

import asyncpg

from kb.compliance.erasure import (
    ErasureResult,
    LocationOutcome,
    PostgresErasureRepository,
    _rowcount,
)
from kb.compliance.pii import DetectedPII, subject_key
from kb.compliance.subject_index import PostgresSubjectIndexer
from kb.database import dsn_from_env
from kb.indexing.deletion import CacheInvalidator
from kb.indexing.store import DocumentStore
from kb.indexing.vector_store import VectorStore

_REDACT = "[已依法删除]"

_CHUNK_LITERAL_SQL = """
SELECT chunk_id FROM chunks
WHERE tenant_id = $1 AND EXISTS (
    SELECT 1 FROM unnest($2::text[]) AS lit
    WHERE chunks.content LIKE '%' || lit || '%'
)
"""

_SET_SUBJECT_SQL = """
UPDATE erasure_requests SET subject_key = $2 WHERE request_id = $1
"""


class SubjectErasureService:
    def __init__(
        self,
        store: DocumentStore,
        vectors: VectorStore,
        cache: CacheInvalidator,
        subject_index: PostgresSubjectIndexer,
        *,
        pepper: str,
        scope_key_hasher: Callable[[str], str] | None = None,
        repository: PostgresErasureRepository | None = None,
        collection: str = "kb_chunks",
        retention_days: int = 15,
        dsn: str | None = None,
    ) -> None:
        if not pepper:
            raise ValueError("pepper 不能为空")
        self._store = store
        self._vectors = vectors
        self._cache = cache
        self._subject_index = subject_index
        self._pepper = pepper
        self._hasher = scope_key_hasher or (lambda value: subject_key(value, pepper))
        self._repo = repository or PostgresErasureRepository()
        self._collection = collection
        self._retention_days = retention_days
        self._dsn = dsn or dsn_from_env()

    async def erase_subject(
        self,
        tenant_id: str,
        identifiers: Sequence[DetectedPII],
        *,
        executor_id: str = "system",
        reviewer_id: str = "system",
        reason: str = "subject_request",
    ) -> ErasureResult:
        keys = [subject_key(item.normalized, self._pepper) for item in identifiers]
        literals = sorted({item.value for item in identifiers})
        request_id = self._request_id(tenant_id, keys)
        state = await self._repo.get_request_state(request_id)
        if state in ("CERTIFIED", "CLOSED"):
            return ErasureResult(
                request_id=request_id,
                state=state,
                certificate_id=self._cert_id(request_id),
                outcomes=(),
            )

        await self._repo.begin_request(
            request_id,
            tenant_id,
            "T1",
            datetime.now(UTC) + timedelta(days=self._retention_days),
        )
        await self._execute(_SET_SUBJECT_SQL, request_id, keys[0] if keys else None)

        indexed = await self._subject_index.locations(tenant_id, keys)
        chunk_ids = {loc_id for loc_type, loc_id in indexed if loc_type == "chunk"}
        if literals:
            chunk_ids.update(await self._chunks_containing(tenant_id, literals))
        chunk_list = sorted(chunk_ids)

        outcomes = await self._execute_locations(tenant_id, request_id, chunk_list, literals)
        for outcome in outcomes:
            await self._repo.record_scope(tenant_id, request_id, outcome)

        for key in keys:
            await self._repo.put_suppression(tenant_id, "subject", key, reason)
        if chunk_list:
            await self._subject_index.delete_chunk_locations(tenant_id, chunk_list)

        failed = [o for o in outcomes if o.status == "FAILED"]
        state = "PARTIAL_FAILED" if failed else "CLOSED"
        await self._repo.finish_request(request_id, state, datetime.now(UTC))
        if not failed:
            await self._write_certificate(
                tenant_id, request_id, outcomes, keys, executor_id, reviewer_id
            )
        return ErasureResult(
            request_id=request_id,
            state=state,
            certificate_id=self._cert_id(request_id),
            outcomes=tuple(outcomes),
        )

    async def _execute_locations(
        self,
        tenant_id: str,
        request_id: str,
        chunk_ids: list[str],
        literals: list[str],
    ) -> list[LocationOutcome]:
        outcomes: list[LocationOutcome] = [
            LocationOutcome("raw_object", request_id, "purge", "SKIPPED", "未配置对象存储")
        ]

        # cache → vector → chunk
        for chunk_id in chunk_ids:
            self._cache.invalidate_chunk(chunk_id)
        outcomes.append(
            LocationOutcome(
                "cache",
                request_id,
                "purge",
                "PHYSICAL_DONE",
                f"失效 {len(chunk_ids)} 个 chunk 缓存",
            )
        )
        try:
            await self._vectors.delete_chunks(self._collection, chunk_ids)
            outcomes.append(
                LocationOutcome(
                    "vector",
                    request_id,
                    "purge",
                    "PHYSICAL_DONE",
                    f"删除 {len(chunk_ids)} 个向量",
                )
            )
        except Exception as exc:  # noqa: BLE001
            outcomes.append(LocationOutcome("vector", request_id, "purge", "FAILED", str(exc)))

        try:
            await self._store.delete_chunks(tenant_id, chunk_ids)
            outcomes.append(
                LocationOutcome(
                    "chunk",
                    request_id,
                    "purge",
                    "PHYSICAL_DONE",
                    f"删除 {len(chunk_ids)} 个 chunk",
                )
            )
        except Exception as exc:  # noqa: BLE001
            outcomes.append(LocationOutcome("chunk", request_id, "purge", "FAILED", str(exc)))

        outcomes.append(
            self._redaction_outcome(
                "session",
                request_id,
                await self._redact_sessions(tenant_id, literals),
                "redact",
            )
        )
        outcomes.append(
            self._redaction_outcome(
                "answer",
                request_id,
                await self._redact_answers(tenant_id, literals),
                "redact",
            )
        )
        outcomes.append(
            LocationOutcome(
                "audit",
                request_id,
                "crypto_shred",
                "SKIPPED",
                "未接入 KMS/主体级 DEK",
            )
        )
        outcomes.append(
            LocationOutcome(
                "backup",
                request_id,
                "crypto_shred",
                "SKIPPED",
                "未接入备份介质与密钥销毁",
            )
        )
        outcomes.append(
            self._redaction_outcome(
                "curated_qa",
                request_id,
                await self._redact_curated(tenant_id, literals),
                "redact",
            )
        )
        try:
            deleted = await self._delete_eval_cases(tenant_id, literals)
            outcomes.append(
                LocationOutcome(
                    "eval_case",
                    request_id,
                    "purge",
                    "PHYSICAL_DONE",
                    f"删除 {deleted} 个用例",
                )
            )
        except Exception as exc:  # noqa: BLE001
            outcomes.append(LocationOutcome("eval_case", request_id, "purge", "FAILED", str(exc)))
        return outcomes

    @staticmethod
    def _redaction_outcome(
        location_type: str, request_id: str, count: int, action: str
    ) -> LocationOutcome:
        return LocationOutcome(
            location_type, request_id, action, "PHYSICAL_DONE", f"脱敏 {count} 行"
        )

    async def _chunks_containing(self, tenant_id: str, literals: list[str]) -> set[str]:
        rows = await self._fetch(_CHUNK_LITERAL_SQL, tenant_id, literals)
        return {str(row["chunk_id"]) for row in rows}

    async def _redact_sessions(self, tenant_id: str, literals: list[str]) -> int:
        total = 0
        for literal in literals:
            total += _rowcount(
                await self._execute(
                    """
                    UPDATE queries SET raw_text = replace(raw_text, $2, $3),
                                       rewritten_text = replace(rewritten_text, $2, $3)
                    WHERE tenant_id = $1 AND (raw_text LIKE '%' || $2 || '%'
                                              OR rewritten_text LIKE '%' || $2 || '%')
                    """,
                    tenant_id,
                    literal,
                    _REDACT,
                )
            )
        return total

    async def _redact_answers(self, tenant_id: str, literals: list[str]) -> int:
        total = 0
        for literal in literals:
            total += _rowcount(
                await self._execute(
                    """
                    UPDATE answers SET text = replace(text, $2, $3)
                    WHERE tenant_id = $1 AND text LIKE '%' || $2 || '%'
                    """,
                    tenant_id,
                    literal,
                    _REDACT,
                )
            )
        return total

    async def _redact_curated(self, tenant_id: str, literals: list[str]) -> int:
        total = 0
        for literal in literals:
            total += _rowcount(
                await self._execute(
                    """
                    UPDATE curated_qa
                    SET answer_text = replace(answer_text, $2, $3),
                        question_pattern = replace(question_pattern, $2, $3),
                        review_state = 'needs_review'
                    WHERE tenant_id = $1
                      AND (answer_text LIKE '%' || $2 || '%'
                           OR question_pattern LIKE '%' || $2 || '%')
                    """,
                    tenant_id,
                    literal,
                    _REDACT,
                )
            )
        return total

    async def _delete_eval_cases(self, tenant_id: str, literals: list[str]) -> int:
        total = 0
        for literal in literals:
            total += _rowcount(
                await self._execute(
                    """
                    DELETE FROM eval_cases
                    WHERE tenant_id = $1
                      AND (question LIKE '%' || $2 || '%'
                           OR expected_answer LIKE '%' || $2 || '%')
                    """,
                    tenant_id,
                    literal,
                )
            )
        return total

    async def _write_certificate(
        self,
        tenant_id: str,
        request_id: str,
        outcomes: list[LocationOutcome],
        keys: list[str],
        executor_id: str,
        reviewer_id: str,
    ) -> None:
        summary = json.dumps(
            [
                {
                    "location_type": o.location_type,
                    "action": o.action,
                    "status": o.status,
                    "detail": o.detail,
                }
                for o in outcomes
            ],
            ensure_ascii=False,
            sort_keys=True,
        )
        skipped = [o for o in outcomes if o.status == "SKIPPED"]
        uncovered = "未覆盖位置：" + "；".join(f"{o.location_type}（{o.detail}）" for o in skipped)
        uncovered += "；PII 召回上限受识别模型限制，另含兜底字面检索"
        method = (
            "主体标识归一化后 HMAC 精确匹配 subject_index；"
            "并以明文标识做兜底全文检索；会话/答案/精选问答字面脱敏，评测集命中即删。"
        )
        cert_hash = hashlib.sha256(
            f"{request_id}\x00{summary}\x00{method}\x00{uncovered}".encode()
        ).hexdigest()
        await self._repo.write_certificate(
            self._cert_id(request_id),
            tenant_id,
            request_id,
            summary,
            method,
            uncovered,
            executor_id,
            reviewer_id,
            cert_hash,
        )

    async def _fetch(self, sql: str, *args: object) -> list[dict[str, object]]:
        conn = await asyncpg.connect(self._dsn)
        try:
            rows = await conn.fetch(sql, *args)
        finally:
            await conn.close()
        return [dict(row) for row in rows]

    async def _execute(self, sql: str, *args: object) -> str:
        conn = await asyncpg.connect(self._dsn)
        try:
            return str(await conn.execute(sql, *args))
        finally:
            await conn.close()

    @staticmethod
    def _request_id(tenant_id: str, keys: list[str]) -> str:
        digest = hashlib.sha256(
            "\x00".join([tenant_id, *sorted(keys), "subject"]).encode()
        ).hexdigest()[:24]
        return f"era_{digest}"

    @staticmethod
    def _cert_id(request_id: str) -> str:
        return f"cert_{hashlib.sha256(request_id.encode()).hexdigest()[:24]}"


__all__ = ["SubjectErasureService"]

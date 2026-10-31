"""T-1 数据主体删除 —— 主体反向索引 + 兜底字面检索 + 正文脱敏。

覆盖范围（诚实说明）：
- chunk / vector / cache：命中主体索引或含明文标识的 chunk 直接硬删
- session / answer / curated_qa：优先按 subject_index 定位，重新识别并按主体
  HMAC 脱敏；未命中再按提供的明文标识做字面替换（[已依法删除]）
- eval_case：优先按 subject_index 删除，未命中再按明文标识兜底
- raw_object：SKIPPED（未配置对象存储）
- audit / backup：有主体 DEK 时销毁并记录（软件 KEK，生产需 KMS）；加密写入与备份轮转未验证

索引写入现状：chunk 由索引流水线写入；answer 由 ChatService 写入；
session / curated_qa / eval_case 的写入路径尚未实现，当前仍依赖明文兜底检索。

召回上限 = PII 识别召回率 + 兜底字面检索命中率，无法宣称 100%（设计 §4.2）。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta

import asyncpg

from kb.compliance.backup import BackupRotation, UnconfiguredBackupRotation
from kb.compliance.crypto import KeyManager
from kb.compliance.erasure import (
    ErasureResult,
    LocationOutcome,
    PostgresErasureRepository,
    ScopeStatus,
    _rowcount,
)
from kb.compliance.pii import DetectedPII, detect_pii, subject_key
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


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _redact_text(text: str, keys: list[str], pepper: str) -> str:
    """按主体 HMAC 重新识别并替换，避免依赖调用方给出的原文写法。"""
    key_set = set(keys)
    for item in detect_pii(text):
        if subject_key(item.normalized, pepper) in key_set:
            text = text.replace(item.value, _REDACT)
    return text


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
        key_manager: KeyManager | None = None,
        backup: BackupRotation | None = None,
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
        self._key_manager = key_manager
        self._backup: BackupRotation = backup or UnconfiguredBackupRotation()

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

        outcomes = await self._execute_locations(
            tenant_id, request_id, chunk_list, literals, keys, indexed
        )
        for outcome in outcomes:
            await self._repo.record_scope(tenant_id, request_id, outcome)

        for key in keys:
            await self._repo.put_suppression(tenant_id, "subject", key, reason)
        if chunk_list:
            await self._subject_index.delete_chunk_locations(tenant_id, chunk_list)
        succeeded = {
            outcome.location_type
            for outcome in outcomes
            if outcome.status in ("PHYSICAL_DONE", "LOGICAL_DONE")
        }
        indexed_non_chunk = [
            (location_type, location_id)
            for location_type, location_id in indexed
            if location_type != "chunk" and location_type in succeeded
        ]
        await self._subject_index.delete_locations(tenant_id, indexed_non_chunk)

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
        keys: list[str],
        indexed: list[tuple[str, str]],
    ) -> list[LocationOutcome]:
        by_type: dict[str, set[str]] = {}
        for location_type, location_id in indexed:
            by_type.setdefault(location_type, set()).add(location_id)
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
                await self._redact_sessions(
                    tenant_id, literals, sorted(by_type.get("session", ())), keys
                ),
                "redact",
            )
        )
        outcomes.append(
            self._redaction_outcome(
                "answer",
                request_id,
                await self._redact_answers(
                    tenant_id, literals, sorted(by_type.get("answer", ())), keys
                ),
                "redact",
            )
        )
        destroyed = await self._destroy_keys(tenant_id, keys)
        if destroyed:
            key_status: ScopeStatus = "LOGICAL_DONE"
            audit_detail = "DEK 已销毁（软件 KEK；生产需 KMS）"
        else:
            key_status = "SKIPPED"
            audit_detail = "该主体无 DEK 记录或未接入 KMS"
        outcomes.append(
            LocationOutcome("audit", request_id, "crypto_shred", key_status, audit_detail)
        )
        backup_outcome = await self._notify_backup(tenant_id, keys)
        outcomes.append(
            LocationOutcome(
                "backup",
                request_id,
                "crypto_shred",
                backup_outcome[0],
                backup_outcome[1],
            )
        )
        outcomes.append(
            self._redaction_outcome(
                "curated_qa",
                request_id,
                await self._redact_curated(
                    tenant_id, literals, sorted(by_type.get("curated_qa", ())), keys
                ),
                "redact",
            )
        )
        try:
            deleted = await self._delete_eval_cases(
                tenant_id, literals, sorted(by_type.get("eval_case", ()))
            )
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

    async def _notify_backup(self, tenant_id: str, keys: list[str]) -> tuple[ScopeStatus, str]:
        if not keys:
            return ("SKIPPED", "无主体标识，未登记备份轮转")
        statuses: list[ScopeStatus] = []
        details: list[str] = []
        expires: list[str] = []
        for key in keys:
            outcome = await self._backup.notify_erasure(
                tenant_id, scope_type="subject", scope_key=key
            )
            statuses.append(outcome.status)
            details.append(outcome.detail)
            if outcome.expires_at is not None:
                expires.append(outcome.expires_at.date().isoformat())
        if "FAILED" in statuses:
            status: ScopeStatus = "FAILED"
        elif "LOGICAL_DONE" in statuses:
            status = "LOGICAL_DONE"
        else:
            status = "SKIPPED"
        detail = "；".join(dict.fromkeys(details))
        if expires:
            detail = f"{detail}（最晚到期 {max(expires)}）"
        return (status, detail)

    async def _destroy_keys(self, tenant_id: str, keys: list[str]) -> bool:
        if self._key_manager is None:
            return False
        destroyed = False
        for key in keys:
            if await self._key_manager.destroy(tenant_id, key, "PIPL-47-subject-erasure"):
                destroyed = True
        return destroyed

    async def _chunks_containing(self, tenant_id: str, literals: list[str]) -> set[str]:
        rows = await self._fetch(_CHUNK_LITERAL_SQL, tenant_id, literals)
        return {str(row["chunk_id"]) for row in rows}

    async def _redact_sessions(
        self,
        tenant_id: str,
        literals: list[str],
        session_ids: list[str],
        keys: list[str],
    ) -> int:
        total = 0
        if session_ids:
            rows = await self._fetch(
                """
                SELECT query_id, raw_text, rewritten_text FROM queries
                WHERE tenant_id = $1 AND session_id = ANY($2::text[])
                  AND (raw_text IS NOT NULL OR rewritten_text IS NOT NULL)
                """,
                tenant_id,
                session_ids,
            )
            for row in rows:
                raw = _optional_text(row["raw_text"])
                rewritten = _optional_text(row["rewritten_text"])
                new_raw = _redact_text(raw, keys, self._pepper) if raw is not None else None
                new_rewritten = (
                    _redact_text(rewritten, keys, self._pepper) if rewritten is not None else None
                )
                if new_raw != raw or new_rewritten != rewritten:
                    total += _rowcount(
                        await self._execute(
                            """
                            UPDATE queries SET raw_text = $3, rewritten_text = $4
                            WHERE tenant_id = $1 AND query_id = $2
                            """,
                            tenant_id,
                            row["query_id"],
                            new_raw,
                            new_rewritten,
                        )
                    )
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

    async def _redact_answers(
        self,
        tenant_id: str,
        literals: list[str],
        answer_ids: list[str],
        keys: list[str],
    ) -> int:
        total = 0
        if answer_ids:
            rows = await self._fetch(
                "SELECT answer_id, text FROM answers "
                "WHERE tenant_id = $1 AND answer_id = ANY($2::text[])",
                tenant_id,
                answer_ids,
            )
            for row in rows:
                text = _optional_text(row["text"])
                new_text = _redact_text(text, keys, self._pepper) if text is not None else text
                if new_text != text:
                    total += _rowcount(
                        await self._execute(
                            "UPDATE answers SET text = $3 WHERE tenant_id = $1 AND answer_id = $2",
                            tenant_id,
                            row["answer_id"],
                            new_text,
                        )
                    )
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

    async def _redact_curated(
        self,
        tenant_id: str,
        literals: list[str],
        qa_ids: list[str],
        keys: list[str],
    ) -> int:
        total = 0
        if qa_ids:
            rows = await self._fetch(
                "SELECT qa_id, answer_text, question_pattern FROM curated_qa "
                "WHERE tenant_id = $1 AND qa_id = ANY($2::text[])",
                tenant_id,
                qa_ids,
            )
            for row in rows:
                answer_text = _optional_text(row["answer_text"])
                question = _optional_text(row["question_pattern"])
                new_answer = (
                    _redact_text(answer_text, keys, self._pepper)
                    if answer_text is not None
                    else answer_text
                )
                new_question = (
                    _redact_text(question, keys, self._pepper) if question is not None else question
                )
                if new_answer != answer_text or new_question != question:
                    total += _rowcount(
                        await self._execute(
                            """
                            UPDATE curated_qa
                            SET answer_text = $3, question_pattern = $4,
                                review_state = 'needs_review'
                            WHERE tenant_id = $1 AND qa_id = $2
                            """,
                            tenant_id,
                            row["qa_id"],
                            new_answer,
                            new_question,
                        )
                    )
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

    async def _delete_eval_cases(
        self, tenant_id: str, literals: list[str], case_ids: list[str]
    ) -> int:
        total = 0
        if case_ids:
            total += _rowcount(
                await self._execute(
                    "DELETE FROM eval_cases WHERE tenant_id = $1 AND case_id = ANY($2::text[])",
                    tenant_id,
                    case_ids,
                )
            )
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
        uncovered += (
            "；PII 召回上限受识别模型限制，另含兜底字面检索"
            "；加密擦除默认软件 KEK，生产须接 KMS（A-20）；"
            "备份轮转为组件级验证（A-30），真实介质待客户环境验收"
        )
        method = (
            "主体标识归一化后 HMAC 精确匹配 subject_index；"
            "命中位置按主体重新识别并脱敏（会话/答案/精选问答），评测集命中即删；"
            "未命中再以明文标识做兜底全文检索。"
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

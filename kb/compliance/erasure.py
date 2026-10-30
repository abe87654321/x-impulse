"""FR-12-04 贯穿式删除 —— 文档级（T-2/T-3/T-4）。

本实现覆盖十处位置中**当前基础设施能够触及**的部分，并对做不到的位置
显式记为 SKIPPED 与原因，写入删除证明的「未覆盖范围声明」。不声称 100% 清除。

幂等：同一 (tenant_id, doc_id) 的请求 ID 固定；已 CLOSED 的请求再次提交
直接返回既有结果，不重复执行。

未实现（需外部基础设施，见删除证明）：
- 原文对象存储、备份介质：项目尚无对象存储/备份接入
- 审计日志加密擦除：尚无 KMS / 主体级 DEK
- 会话/答案正文脱敏：文档级删除无法定位主体文本（T-1 需要 subject_index）
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol

import asyncpg

from kb.compliance.restriction import RestrictionService
from kb.database import dsn_from_env
from kb.indexing.deletion import CacheInvalidator
from kb.indexing.store import DocumentStore
from kb.indexing.suppression import document_scope_key
from kb.indexing.vector_store import VectorStore

ScopeStatus = Literal["PENDING", "LOGICAL_DONE", "PHYSICAL_DONE", "FAILED", "SKIPPED"]

_LOCATIONS = (
    "raw_object",
    "chunk",
    "vector",
    "cache",
    "session",
    "answer",
    "audit",
    "backup",
    "curated_qa",
    "eval_case",
)

_ACTIONS = {
    "raw_object": "purge",
    "chunk": "purge",
    "vector": "purge",
    "cache": "purge",
    "session": "redact",
    "answer": "redact",
    "audit": "crypto_shred",
    "backup": "crypto_shred",
    "curated_qa": "redact",
    "eval_case": "purge",
}


@dataclass(frozen=True)
class LocationOutcome:
    location_type: str
    location_id: str
    action: str
    status: ScopeStatus
    detail: str | None = None


@dataclass(frozen=True)
class ErasureResult:
    request_id: str
    state: str
    certificate_id: str
    outcomes: tuple[LocationOutcome, ...]


class ErasureRepository(Protocol):
    async def get_request_state(self, request_id: str) -> str | None: ...

    async def get_document_identity(
        self, tenant_id: str, doc_id: str
    ) -> tuple[str, str] | None: ...

    async def begin_request(
        self, request_id: str, tenant_id: str, trigger_type: str, deadline: datetime
    ) -> None: ...

    async def record_scope(
        self,
        tenant_id: str,
        request_id: str,
        outcome: LocationOutcome,
    ) -> None: ...

    async def finish_request(self, request_id: str, state: str, completed_at: datetime) -> None: ...

    async def put_suppression(
        self, tenant_id: str, scope_type: str, scope_key: str, reason: str
    ) -> None: ...

    async def redact_curated_qa(self, tenant_id: str, doc_id: str, chunk_ids: list[str]) -> int: ...

    async def scrub_eval_cases(self, tenant_id: str, chunk_ids: list[str]) -> int: ...

    async def write_certificate(
        self,
        cert_id: str,
        tenant_id: str,
        request_id: str,
        scope_summary: str,
        method_statement: str,
        uncovered_statement: str,
        executor_id: str,
        reviewer_id: str,
        cert_hash: str,
    ) -> None: ...


_BEGIN_SQL = """
INSERT INTO erasure_requests
    (request_id, tenant_id, trigger_type, requester_verified, state, deadline_at)
VALUES ($1, $2, $3, true, 'EXECUTING', $4)
ON CONFLICT (request_id) DO NOTHING
"""

_STATE_SQL = "SELECT state FROM erasure_requests WHERE request_id = $1"

_IDENTITY_SQL = """
SELECT source_id, external_id FROM documents
WHERE tenant_id = $1 AND doc_id = $2
"""

_SCOPE_SQL = """
INSERT INTO erasure_scopes
    (scope_id, tenant_id, request_id, location_type, location_id, action, status,
     attempts, logical_completed_at, physical_completed_at, last_error)
VALUES ($1, $2, $3, $4, $5, $6, $7, 1,
        CASE WHEN $7 IN ('LOGICAL_DONE','PHYSICAL_DONE') THEN now() ELSE NULL END,
        CASE WHEN $7 = 'PHYSICAL_DONE' THEN now() ELSE NULL END,
        $8)
ON CONFLICT (request_id, location_type, location_id) DO UPDATE SET
    action = EXCLUDED.action,
    status = EXCLUDED.status,
    attempts = erasure_scopes.attempts + 1,
    logical_completed_at = EXCLUDED.logical_completed_at,
    physical_completed_at = EXCLUDED.physical_completed_at,
    last_error = EXCLUDED.last_error
"""

_FINISH_SQL = """
UPDATE erasure_requests SET state = $2, completed_at = $3 WHERE request_id = $1
"""

_SUPPRESS_SQL = """
INSERT INTO suppression_list
    (suppression_id, tenant_id, scope_type, scope_key, reason)
VALUES ($1, $2, $3, $4, $5)
ON CONFLICT (tenant_id, scope_type, scope_key) DO NOTHING
"""

_CURATED_SQL = """
UPDATE curated_qa SET review_state = 'needs_review'
WHERE tenant_id = $1
  AND EXISTS (
      SELECT 1 FROM unnest($2::text[]) AS token
      WHERE curated_qa.citations::text LIKE '%' || token || '%'
  )
"""

_EVAL_SQL = """
UPDATE eval_cases
SET expected_chunk_ids = ARRAY(
    SELECT x FROM unnest(expected_chunk_ids) AS x
    EXCEPT
    SELECT y FROM unnest($2::text[]) AS y
)
WHERE tenant_id = $1 AND expected_chunk_ids && $2::text[]
"""

_CERT_SQL = """
INSERT INTO erasure_certificates
    (cert_id, tenant_id, request_id, scope_summary, method_statement,
     uncovered_scope_statement, executor_id, reviewer_id, cert_hash)
VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7, $8, $9)
"""


def _digest(*parts: str) -> str:
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()[:24]


def request_id_for(tenant_id: str, doc_id: str) -> str:
    return f"era_{_digest(tenant_id, doc_id, 'document')}"


class PostgresErasureRepository:
    def __init__(self, dsn: str | None = None) -> None:
        self._dsn = dsn or dsn_from_env()

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

    async def get_request_state(self, request_id: str) -> str | None:
        rows = await self._fetch(_STATE_SQL, request_id)
        return None if not rows else str(rows[0]["state"])

    async def get_document_identity(self, tenant_id: str, doc_id: str) -> tuple[str, str] | None:
        rows = await self._fetch(_IDENTITY_SQL, tenant_id, doc_id)
        if not rows:
            return None
        return str(rows[0]["source_id"]), str(rows[0]["external_id"])

    async def begin_request(
        self, request_id: str, tenant_id: str, trigger_type: str, deadline: datetime
    ) -> None:
        await self._execute(_BEGIN_SQL, request_id, tenant_id, trigger_type, deadline)

    async def record_scope(self, tenant_id: str, request_id: str, outcome: LocationOutcome) -> None:
        await self._execute(
            _SCOPE_SQL,
            "esc_" + _digest(request_id, outcome.location_type, outcome.location_id),
            tenant_id,
            request_id,
            outcome.location_type,
            outcome.location_id,
            outcome.action,
            outcome.status,
            outcome.detail,
        )

    async def finish_request(self, request_id: str, state: str, completed_at: datetime) -> None:
        await self._execute(_FINISH_SQL, request_id, state, completed_at)

    async def put_suppression(
        self, tenant_id: str, scope_type: str, scope_key: str, reason: str
    ) -> None:
        await self._execute(
            _SUPPRESS_SQL,
            "sup_" + _digest(tenant_id, scope_type, scope_key),
            tenant_id,
            scope_type,
            scope_key,
            reason,
        )

    async def redact_curated_qa(self, tenant_id: str, doc_id: str, chunk_ids: list[str]) -> int:
        result = await self._execute(_CURATED_SQL, tenant_id, [doc_id, *chunk_ids])
        return _rowcount(result)

    async def scrub_eval_cases(self, tenant_id: str, chunk_ids: list[str]) -> int:
        if not chunk_ids:
            return 0
        result = await self._execute(_EVAL_SQL, tenant_id, list(chunk_ids))
        return _rowcount(result)

    async def write_certificate(
        self,
        cert_id: str,
        tenant_id: str,
        request_id: str,
        scope_summary: str,
        method_statement: str,
        uncovered_statement: str,
        executor_id: str,
        reviewer_id: str,
        cert_hash: str,
    ) -> None:
        await self._execute(
            _CERT_SQL,
            cert_id,
            tenant_id,
            request_id,
            scope_summary,
            method_statement,
            uncovered_statement,
            executor_id,
            reviewer_id,
            cert_hash,
        )


class DocumentErasureService:
    def __init__(
        self,
        repository: ErasureRepository,
        store: DocumentStore,
        vectors: VectorStore,
        cache: CacheInvalidator,
        *,
        scope_key_hasher: Callable[[str], str],
        collection: str = "kb_chunks",
        retention_days: int = 15,
        restrictions: RestrictionService | None = None,
    ) -> None:
        self._repo = repository
        self._store = store
        self._vectors = vectors
        self._cache = cache
        self._hasher = scope_key_hasher
        self._collection = collection
        self._retention_days = retention_days
        self._restrictions = restrictions

    async def erase_document(
        self,
        tenant_id: str,
        doc_id: str,
        *,
        trigger_type: str = "T2",
        executor_id: str = "system",
        reviewer_id: str = "system",
        reason: str = "erasure_request",
    ) -> ErasureResult:
        request_id = request_id_for(tenant_id, doc_id)
        state = await self._repo.get_request_state(request_id)
        if state in ("CERTIFIED", "CLOSED"):
            return ErasureResult(
                request_id=request_id,
                state=state,
                certificate_id=self._cert_id(request_id),
                outcomes=(),
            )

        identity = await self._repo.get_document_identity(tenant_id, doc_id)
        if identity is None:
            raise ValueError(f"文档不存在：{doc_id}")
        source_id, external_id = identity

        await self._repo.begin_request(
            request_id,
            tenant_id,
            trigger_type,
            datetime.now(UTC) + timedelta(days=self._retention_days),
        )
        if self._restrictions is not None:
            hold = await self._restrictions.active_hold(tenant_id, doc_id, datetime.now(UTC))
            if hold is not None:
                await self._restrictions.mark_restricted(tenant_id, doc_id)
                outcomes = [
                    LocationOutcome(
                        location,
                        doc_id,
                        "restrict",
                        "SKIPPED",
                        f"法定保留至 {hold.statutory_until or '未定'}",
                    )
                    for location in _LOCATIONS
                ]
                for outcome in outcomes:
                    await self._repo.record_scope(tenant_id, request_id, outcome)
                await self._repo.finish_request(request_id, "RESTRICTED", datetime.now(UTC))
                return ErasureResult(
                    request_id=request_id,
                    state="RESTRICTED",
                    certificate_id=self._cert_id(request_id),
                    outcomes=tuple(outcomes),
                )

        chunk_ids = await self._store.list_chunk_ids(tenant_id, doc_id)
        outcomes = await self._execute_locations(tenant_id, doc_id, chunk_ids)

        for outcome in outcomes:
            await self._repo.record_scope(tenant_id, request_id, outcome)

        await self._repo.put_suppression(
            tenant_id,
            "document",
            self._hasher(document_scope_key(source_id, external_id)),
            reason,
        )

        failed = [o for o in outcomes if o.status == "FAILED"]
        state = "PARTIAL_FAILED" if failed else "CLOSED"
        completed_at = datetime.now(UTC)
        await self._repo.finish_request(request_id, state, completed_at)
        if not failed:
            await self._write_certificate(tenant_id, request_id, outcomes, executor_id, reviewer_id)
        return ErasureResult(
            request_id=request_id,
            state=state,
            certificate_id=self._cert_id(request_id),
            outcomes=tuple(outcomes),
        )

    async def _execute_locations(
        self, tenant_id: str, doc_id: str, chunk_ids: list[str]
    ) -> list[LocationOutcome]:
        outcomes: list[LocationOutcome] = []

        # 1. 原文缓存：项目尚无对象存储
        outcomes.append(
            LocationOutcome(
                "raw_object",
                doc_id,
                _ACTIONS["raw_object"],
                "SKIPPED",
                "未配置对象存储",
            )
        )

        # 4. 检索缓存：先切断可达性
        for chunk_id in chunk_ids:
            self._cache.invalidate_chunk(chunk_id)
        outcomes.append(
            LocationOutcome(
                "cache",
                doc_id,
                _ACTIONS["cache"],
                "PHYSICAL_DONE",
                f"失效 {len(chunk_ids)} 个 chunk 缓存",
            )
        )

        # 3. 向量索引
        try:
            await self._vectors.delete_document(
                self._collection, tenant_id=tenant_id, doc_id=doc_id
            )
            outcomes.append(LocationOutcome("vector", doc_id, _ACTIONS["vector"], "PHYSICAL_DONE"))
        except Exception as exc:  # noqa: BLE001
            outcomes.append(
                LocationOutcome("vector", doc_id, _ACTIONS["vector"], "FAILED", str(exc))
            )

        # 2. chunk（含 annotations、acl、citations 悬空、chunk_vectors）
        try:
            await self._store.delete_document(tenant_id, doc_id)
            outcomes.append(LocationOutcome("chunk", doc_id, _ACTIONS["chunk"], "PHYSICAL_DONE"))
        except Exception as exc:  # noqa: BLE001
            outcomes.append(LocationOutcome("chunk", doc_id, _ACTIONS["chunk"], "FAILED", str(exc)))

        # 5/6. 会话与答案正文：文档级删除无法定位主体文本
        outcomes.append(
            LocationOutcome(
                "session",
                doc_id,
                _ACTIONS["session"],
                "SKIPPED",
                "文档级删除无主体定位；未改写会话正文",
            )
        )
        outcomes.append(
            LocationOutcome(
                "answer",
                doc_id,
                _ACTIONS["answer"],
                "SKIPPED",
                "引用已置空并保留快照；答案正文未改写",
            )
        )

        # 7/8. 审计与备份：未接入密钥销毁
        outcomes.append(
            LocationOutcome("audit", doc_id, _ACTIONS["audit"], "SKIPPED", "未接入 KMS/主体级 DEK")
        )
        outcomes.append(
            LocationOutcome(
                "backup", doc_id, _ACTIONS["backup"], "SKIPPED", "未接入备份介质与密钥销毁"
            )
        )

        # 9. 精选问答：标脏并重新审核
        try:
            count = await self._repo.redact_curated_qa(tenant_id, doc_id, chunk_ids)
            outcomes.append(
                LocationOutcome(
                    "curated_qa",
                    doc_id,
                    _ACTIONS["curated_qa"],
                    "PHYSICAL_DONE",
                    f"标记 {count} 条待复审",
                )
            )
        except Exception as exc:  # noqa: BLE001
            outcomes.append(
                LocationOutcome("curated_qa", doc_id, _ACTIONS["curated_qa"], "FAILED", str(exc))
            )

        # 10. 评测集：移除期望 chunk
        try:
            count = await self._repo.scrub_eval_cases(tenant_id, chunk_ids)
            outcomes.append(
                LocationOutcome(
                    "eval_case",
                    doc_id,
                    _ACTIONS["eval_case"],
                    "PHYSICAL_DONE",
                    f"清理 {count} 个用例",
                )
            )
        except Exception as exc:  # noqa: BLE001
            outcomes.append(
                LocationOutcome("eval_case", doc_id, _ACTIONS["eval_case"], "FAILED", str(exc))
            )

        return outcomes

    async def _write_certificate(
        self,
        tenant_id: str,
        request_id: str,
        outcomes: list[LocationOutcome],
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
        method = (
            "按 doc_id 精确定位；先失效缓存与向量，再硬删 chunk；"
            "精选问答标脏复审、评测集移除期望 chunk；写入抑制清单。"
        )
        cert_id = self._cert_id(request_id)
        cert_hash = hashlib.sha256(
            f"{request_id}\x00{summary}\x00{method}\x00{uncovered}".encode()
        ).hexdigest()
        await self._repo.write_certificate(
            cert_id,
            tenant_id,
            request_id,
            summary,
            method,
            uncovered,
            executor_id,
            reviewer_id,
            cert_hash,
        )

    @staticmethod
    def _cert_id(request_id: str) -> str:
        return f"cert_{hashlib.sha256(request_id.encode()).hexdigest()[:24]}"


def _rowcount(status: str) -> int:
    parts = status.split()
    return int(parts[-1]) if parts and parts[-1].isdigit() else 0


__all__ = [
    "DocumentErasureService",
    "ErasureRepository",
    "ErasureResult",
    "LocationOutcome",
    "PostgresErasureRepository",
    "request_id_for",
]

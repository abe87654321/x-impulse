"""FR-12-18 限制处理与保留期到期清除。

限制处理 = 停止除存储与安全保护以外的一切处理：
- 文档状态置为 `restricted`，检索只取 `indexed`，因此召回、缓存命中、二次校验都会排除它。
- 删除请求遇到有效 hold 时不执行清除，状态转 `RESTRICTED`。
- `statutory_until` 到期后由 `purge_expired` 自动释放并执行真正的删除（T-3）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Protocol

import asyncpg

if TYPE_CHECKING:
    from kb.compliance.erasure import ErasureResult
from kb.database import dsn_from_env

_DOCUMENT_LOCATION = "document"

_ENSURE_LOCATION_SQL = """
INSERT INTO erasure_location_types (location_type, display_name, purge_method, is_active)
VALUES ('document', '文档（限制处理）', 'hard_delete', true)
ON CONFLICT (location_type) DO NOTHING
"""

_INSERT_HOLD_SQL = """
INSERT INTO restriction_holds
    (hold_id, tenant_id, location_type, location_id, reason, statutory_until, auto_purge_scheduled)
VALUES ($1, $2, $3, $4, $5, $6, $7)
"""

_ACTIVE_SQL = """
SELECT hold_id, tenant_id, location_id, reason, statutory_until
FROM restriction_holds
WHERE tenant_id = $1 AND location_type = $2 AND location_id = $3
  AND (statutory_until IS NULL OR statutory_until > $4)
ORDER BY created_at DESC
LIMIT 1
"""

_EXPIRED_SQL = """
SELECT hold_id, tenant_id, location_id, reason, statutory_until
FROM restriction_holds
WHERE auto_purge_scheduled = true
  AND statutory_until IS NOT NULL
  AND statutory_until <= $1
"""

_DELETE_HOLD_SQL = "DELETE FROM restriction_holds WHERE hold_id = $1"

_SET_RESTRICTED_SQL = """
UPDATE documents SET status = 'restricted' WHERE tenant_id = $1 AND doc_id = $2
"""


@dataclass(frozen=True)
class HoldInfo:
    hold_id: str
    tenant_id: str
    location_id: str
    reason: str
    statutory_until: datetime | None


class DocumentEraser(Protocol):
    async def erase_document(
        self, tenant_id: str, doc_id: str, *, trigger_type: str = "T2"
    ) -> ErasureResult: ...


class RestrictionService:
    def __init__(self, dsn: str | None = None) -> None:
        self._dsn = dsn or dsn_from_env()

    async def hold_document(
        self,
        tenant_id: str,
        doc_id: str,
        *,
        reason: str,
        statutory_until: datetime | None,
        auto_purge: bool = True,
        hold_id: str | None = None,
    ) -> str:
        hold = hold_id or f"hold_{_digest(tenant_id, doc_id, reason)}"
        conn = await asyncpg.connect(self._dsn)
        try:
            async with conn.transaction():
                await conn.execute(_ENSURE_LOCATION_SQL)
                await conn.execute(
                    _INSERT_HOLD_SQL,
                    hold,
                    tenant_id,
                    _DOCUMENT_LOCATION,
                    doc_id,
                    reason,
                    statutory_until,
                    auto_purge,
                )
                await conn.execute(_SET_RESTRICTED_SQL, tenant_id, doc_id)
        finally:
            await conn.close()
        return hold

    async def mark_restricted(self, tenant_id: str, doc_id: str) -> None:
        conn = await asyncpg.connect(self._dsn)
        try:
            await conn.execute(_SET_RESTRICTED_SQL, tenant_id, doc_id)
        finally:
            await conn.close()

    async def active_hold(self, tenant_id: str, doc_id: str, now: datetime) -> HoldInfo | None:
        conn = await asyncpg.connect(self._dsn)
        try:
            row = await conn.fetchrow(_ACTIVE_SQL, tenant_id, _DOCUMENT_LOCATION, doc_id, now)
        finally:
            await conn.close()
        return None if row is None else _to_hold(row)

    async def list_expired(self, now: datetime) -> list[HoldInfo]:
        """供调度器 dry-run：只列出到期 hold，不释放也不删除。"""
        return await self._expired(now)

    async def purge_expired(self, eraser: DocumentEraser, now: datetime) -> list[ErasureResult]:
        """保留期届满：释放 hold，然后执行 T-3 真删。"""
        holds = await self._expired(now)
        results: list[ErasureResult] = []
        for hold in holds:
            await self._delete_hold(hold.hold_id)
            results.append(
                await eraser.erase_document(hold.tenant_id, hold.location_id, trigger_type="T3")
            )
        return results

    async def _expired(self, now: datetime) -> list[HoldInfo]:
        conn = await asyncpg.connect(self._dsn)
        try:
            rows = await conn.fetch(_EXPIRED_SQL, now)
        finally:
            await conn.close()
        return [_to_hold(row) for row in rows]

    async def _delete_hold(self, hold_id: str) -> None:
        conn = await asyncpg.connect(self._dsn)
        try:
            await conn.execute(_DELETE_HOLD_SQL, hold_id)
        finally:
            await conn.close()


def _to_hold(row: dict[str, object]) -> HoldInfo:
    until = row["statutory_until"]
    return HoldInfo(
        hold_id=str(row["hold_id"]),
        tenant_id=str(row["tenant_id"]),
        location_id=str(row["location_id"]),
        reason=str(row["reason"]),
        statutory_until=until if isinstance(until, datetime) else None,
    )


def _digest(*parts: str) -> str:
    import hashlib

    return hashlib.sha256("\x00".join(parts).encode()).hexdigest()[:24]


__all__ = ["DocumentEraser", "HoldInfo", "RestrictionService"]

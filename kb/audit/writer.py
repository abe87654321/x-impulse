"""审计日志追加 —— FR-09-09 哈希链。

只追加、不更新、不删除。每行 `row_hash` 覆盖前一行哈希与本次内容，
`prev_hash` 为空表示链首。

注意：本实现假定单写者（或由上层串行化）。并发追加需要数据库级锁，
否则两个写者会读到同一个 prev_hash 而分叉。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

import asyncpg

from kb.database import dsn_from_env

_LAST_SQL = """
SELECT row_hash FROM audit_log ORDER BY log_id DESC LIMIT 1
"""

_INSERT_SQL = """
INSERT INTO audit_log
    (tenant_id, ts, category, principal_id, actor_id, delegation_chain,
     assurance_level, action, target_type, target_id, payload, prev_hash, row_hash)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb, $12, $13)
RETURNING log_id
"""


@dataclass(frozen=True)
class AuditEvent:
    category: str
    action: str
    tenant_id: str
    principal_id: str | None = None
    actor_id: str | None = None
    delegation_chain: tuple[str, ...] = ()
    assurance_level: str | None = None
    target_type: str | None = None
    target_id: str | None = None
    payload: dict[str, object] | None = None


class AuditStore(Protocol):
    async def append(self, event: AuditEvent, *, now: datetime | None = None) -> str: ...


class PostgresAuditStore:
    def __init__(self, dsn: str | None = None) -> None:
        self._dsn = dsn or dsn_from_env()

    async def append(self, event: AuditEvent, *, now: datetime | None = None) -> str:
        ts = now or datetime.now(UTC)
        payload = event.payload or {}
        payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        conn = await asyncpg.connect(self._dsn)
        try:
            async with conn.transaction():
                row = await conn.fetchrow(_LAST_SQL)
                prev_hash = None if row is None else str(row["row_hash"])
                row_hash = _row_hash(prev_hash, ts, event, payload_json)
                log_id = await conn.fetchval(
                    _INSERT_SQL,
                    event.tenant_id,
                    ts,
                    event.category,
                    event.principal_id,
                    event.actor_id,
                    list(event.delegation_chain),
                    event.assurance_level,
                    event.action,
                    event.target_type,
                    event.target_id,
                    payload_json,
                    prev_hash,
                    row_hash,
                )
        finally:
            await conn.close()
        return str(log_id)


def _row_hash(prev_hash: str | None, ts: datetime, event: AuditEvent, payload_json: str) -> str:
    canonical = "\x00".join(
        [
            prev_hash or "",
            ts.isoformat(),
            event.tenant_id,
            event.category,
            event.action,
            event.principal_id or "",
            event.actor_id or "",
            event.target_type or "",
            event.target_id or "",
            payload_json,
        ]
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


__all__ = ["AuditEvent", "AuditStore", "PostgresAuditStore"]

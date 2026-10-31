"""审计日志追加 —— FR-09-09 哈希链。

只追加、不更新、不删除。每行 `row_hash` 覆盖前一行哈希与本次内容，
`prev_hash` 为空表示链首。

注意：本实现假定单写者（或由上层串行化）。并发追加需要数据库级锁，
否则两个写者会读到同一个 prev_hash 而分叉。
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

import asyncpg

from kb.audit.protection import AuditPayloadError, FieldKeyManager, protect_payload
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
    def __init__(
        self,
        dsn: str | None = None,
        *,
        key_manager: FieldKeyManager | None = None,
        subject_hasher: Callable[[str], str] | None = None,
    ) -> None:
        if key_manager is not None and subject_hasher is None:
            raise ValueError("配置审计加密时必须提供 subject_hasher")
        self._dsn = dsn or dsn_from_env()
        self._key_manager = key_manager
        self._subject_hasher = subject_hasher

    async def append(self, event: AuditEvent, *, now: datetime | None = None) -> str:
        ts = now or datetime.now(UTC)
        payload = await protect_payload(
            event.payload or {},
            tenant_id=event.tenant_id,
            key_manager=self._key_manager,
            subject_hasher=self._subject_hasher,
        )
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


def build_audit_store_from_env(
    dsn: str | None = None, environ: Mapping[str, str] | None = None
) -> PostgresAuditStore:
    """按环境配置装配审计存储；只配一半的加密参数视为配置错误。"""
    env = os.environ if environ is None else environ
    kek_raw = env.get("KB_KEK_BASE64")
    pepper = env.get("KB_AUDIT_PII_PEPPER") or env.get("KB_SUPPRESSION_PEPPER")
    if not kek_raw and not pepper:
        return PostgresAuditStore(dsn)
    if not kek_raw or not pepper:
        raise AuditPayloadError("审计加密需同时配置 KB_KEK_BASE64 与 KB_AUDIT_PII_PEPPER")
    from kb.compliance.crypto import SoftwareKeyManager
    from kb.compliance.pii import subject_key

    try:
        kek = base64.b64decode(kek_raw, validate=True)
    except ValueError as exc:
        raise AuditPayloadError("KB_KEK_BASE64 不是合法 base64") from exc
    return PostgresAuditStore(
        dsn,
        key_manager=SoftwareKeyManager(kek=kek, dsn=dsn),
        subject_hasher=lambda value: subject_key(value, pepper),
    )


__all__ = ["AuditEvent", "AuditStore", "PostgresAuditStore", "build_audit_store_from_env"]

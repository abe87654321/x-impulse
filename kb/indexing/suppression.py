"""抑制清单检查 —— O-1/O-2 的前置闸门。

`scope_key` 在库里是 HMAC，不存明文（schema 注释）。因此本模块不自行哈希，
而是由调用方注入 `key_hasher`，确保与写入抑制清单的一方使用同一密钥。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

import asyncpg

from kb.database import dsn_from_env

ScopeType = str  # document | subject | chunk_hash


def document_scope_key(source_id: str, external_id: str) -> str:
    """与删除链路共用的文档抑制键格式；哈希由 checker/hasher 统一完成。"""
    return f"{source_id}\x00{external_id}"


class SuppressionChecker(Protocol):
    async def is_suppressed(
        self, tenant_id: str, scope_type: ScopeType, scope_key: str
    ) -> bool: ...


class NullSuppressionChecker:
    async def is_suppressed(self, tenant_id: str, scope_type: ScopeType, scope_key: str) -> bool:
        return False


class InMemorySuppressionChecker:
    def __init__(self, suppressed: set[tuple[str, str, str]] | None = None) -> None:
        self._suppressed = suppressed or set()

    async def is_suppressed(self, tenant_id: str, scope_type: ScopeType, scope_key: str) -> bool:
        return (tenant_id, scope_type, scope_key) in self._suppressed


_SUPPRESSED_SQL = """
SELECT 1
FROM suppression_list
WHERE tenant_id = $1 AND scope_type = $2 AND scope_key = $3
  AND (expires_at IS NULL OR expires_at > now())
LIMIT 1
"""


class PostgresSuppressionChecker:
    def __init__(self, key_hasher: Callable[[str], str], dsn: str | None = None) -> None:
        self._hasher = key_hasher
        self._dsn = dsn or dsn_from_env()

    async def is_suppressed(self, tenant_id: str, scope_type: ScopeType, scope_key: str) -> bool:
        conn = await asyncpg.connect(self._dsn)
        try:
            row = await conn.fetchrow(
                _SUPPRESSED_SQL, tenant_id, scope_type, self._hasher(scope_key)
            )
        finally:
            await conn.close()
        return row is not None


__all__ = [
    "document_scope_key",
    "InMemorySuppressionChecker",
    "NullSuppressionChecker",
    "PostgresSuppressionChecker",
    "SuppressionChecker",
]
